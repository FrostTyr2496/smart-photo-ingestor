from __future__ import annotations

from datetime import datetime
from pathlib import Path

from photovault.groups import build_groups
from photovault.scan import FileEntry, classify


def _entries(*rels: str) -> list[FileEntry]:
    return [FileEntry(r, Path("/c") / r, 10, 1_700_000_000_000_000_000, classify(r)) for r in rels]


def _roles(groups) -> list[dict[str, str]]:
    return [{m.entry.rel_path.rsplit("/", 1)[-1]: m.role for m in g.members} for g in groups]


def test_pairs_sidecars_derivatives_and_orphans() -> None:
    es = _entries("d/DSC_1.NEF", "d/DSC_1.JPG", "d/DSC_1.xmp", "d/DSC_1-Edit.tif", "d/DSC_9.xmp")
    groups = build_groups(es, {})
    assert _roles(groups) == [
        {"DSC_1.NEF": "primary", "DSC_1.JPG": "jpeg", "DSC_1.xmp": "sidecar", "DSC_1-Edit.tif": "derivative"},
        {"DSC_9.xmp": "orphan_sidecar"},
    ]


def test_live_photo_and_drone_and_gopro() -> None:
    groups = build_groups(_entries("a/IMG_1.HEIC", "a/IMG_1.AAE", "a/IMG_1.MOV", "b/DJI_1.MP4", "b/DJI_1.SRT",
                                   "c/GX01.MP4", "c/GX01.LRV"), {})
    assert _roles(groups) == [
        {"IMG_1.AAE": "aae", "IMG_1.HEIC": "primary", "IMG_1.MOV": "live_video"},
        {"DJI_1.MP4": "primary", "DJI_1.SRT": "telemetry"},
        {"GX01.LRV": "proxy", "GX01.MP4": "primary"},
    ]


def test_identity_from_metadata_with_fallback_and_mtime() -> None:
    nef, jpg, other = _entries("d/DSC_0042.NEF", "d/DSC_0042.JPG", "PANORAMA/100/DJI_7.DNG")
    meta = {jpg.abs_path: {"Model": "NIKON Z 6_2", "ShutterCount": 5, "DateTimeOriginal": datetime(2026, 1, 1, 20),
                           "CaptureTimeSource": "exif"}}
    g1, g2 = sorted(build_groups([nef, jpg, other], meta), key=lambda g: g.key)
    assert g1.folder_hint == "PANORAMA"
    assert g1.time_source == "mtime"
    assert (g2.model, g2.shutter_count, g2.original_num, g2.time_source) == ("NIKON Z 6_2", 5, 42, "exif")


def test_same_stem_in_two_folders_is_two_groups() -> None:
    assert len(build_groups(_entries("DCIM/100/DSC_0001.NEF", "DCIM/101/DSC_0001.NEF"), {})) == 2
