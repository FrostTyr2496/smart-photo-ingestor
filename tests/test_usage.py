"""Drive usage (idx-13): buckets for files the catalog does not track, and the camera vs other video split."""

from __future__ import annotations

from pathlib import Path

import pytest

from photovault import usage
from photovault.scan import FileEntry, classify, walk_all


def entry(rel: str, size: int = 10) -> FileEntry:
    return FileEntry(rel_path=rel, abs_path=Path("/x") / rel, size=size, mtime_ns=0, kind=classify(rel))


@pytest.mark.parametrize("name, cat", [
    ("Catalog.lrcat", "lightroom"), ("Catalog.lrcat-wal", "lightroom"), ("Catalog.lrcat.lock", "lightroom"),
    ("x.lrprev", "lightroom"), ("DSC_0001.THM", "photo_extras"), ("NIKON001.DSC", "photo_extras"),
    ("Film (2010).mkv", "video_other"), ("clip.TS", "video_other"), ("song.flac", "audio"),
    ("screenshot.png", "image_other"), ("taxes.pdf", "documents"), ("backup.zip", "archives"),
    ("setup.exe", "software"), ("README", "other"), ("data.bin", "other"),
])
def test_category(name: str, cat: str) -> None:
    assert usage.category(name) == cat


@pytest.mark.parametrize("stem, ok", [
    ("DSC_3460", True), ("DJI_0882", True), ("GX010004", True), ("IMG_E0772", True), ("C0001", True),
    ("PXL_20240101_123456789", True), ("Z61_0001", True),
    ("dji_fly_20240101_123456_0001_1700000000_photo", True), ("dji_export_20240101_123456_1", True),
    ("M0001_20240101-123456", True), ("M0001_20240101-123456_2", True),
    ("Inception (2010)", False), ("S01E02", False), ("holiday", False), ("Movie.2010.1080p", False),
])
def test_camera_stem(stem: str, ok: bool) -> None:
    assert usage.camera_stem(stem) is ok


def test_non_camera_videos_uses_cheap_signals_before_exiftool() -> None:
    entries = [
        entry("Trip/DSC_0001.NEF"), entry("Trip/holiday.mov"),               # photos in the folder
        entry("Clips/GoodBit.mp4"), entry("Clips/GoodBit.LRV"),             # camera proxy beside it
        entry("Drone/DJI_0882.MP4"),                                         # camera-style name
        entry("Old/DCIM/100APPLE/edit.mov"),                                 # DCIM on the path
        entry("Films/Phone night.mov"),                                      # ExifTool finds a Make
        entry("Films/Inception (2010).mp4"), entry("Films/Inception (2010).srt"),  # subtitles do not count
        entry("Posters/poster.jpg"), entry("Posters/trailer.mp4"),          # a poster is not a camera photo
    ]
    asked: list[Path] = []

    def read(paths: list[Path]) -> dict[Path, dict]:
        asked.extend(paths)
        return {p: {"Make": "Apple"} for p in paths if "Phone" in p.name}

    out = usage.non_camera_videos(entries, read)
    assert sorted(e.rel_path for e in out) == ["Films/Inception (2010).mp4", "Posters/trailer.mp4"]
    assert sorted(p.name for p in asked) == ["Inception (2010).mp4", "Phone night.mov", "trailer.mp4"]


def test_usage_rows_from_a_walk(tmp_path: Path) -> None:
    root = tmp_path / "drive"
    (root / "Photos").mkdir(parents=True)
    (root / "Photos/DSC_0001.NEF").write_bytes(b"n" * 100)
    (root / "Photos/DSC_0001.THM").write_bytes(b"t" * 5)
    (root / "Cat/Previews.lrdata/A").mkdir(parents=True)
    (root / "Cat/Previews.lrdata/A/x.lrprev").write_bytes(b"p" * 40)
    (root / "Cat/Previews.lrdata/root-pixels.db").write_bytes(b"d" * 7)
    (root / "Cat/Cat.lrcat").write_bytes(b"c" * 30)
    (root / "Movies").mkdir()
    (root / "Movies/Film.mkv").write_bytes(b"m" * 300)
    (root / "Movies/Film (2010).mp4").write_bytes(b"v" * 200)
    (root / "Music").mkdir()
    (root / "Music/a.mp3").write_bytes(b"a" * 50)
    walked, lrdata = walk_all(root)
    assert [p.name for p in lrdata] == ["Previews.lrdata"]
    rows = usage.usage_rows(walked, lrdata, root, lambda paths: {})
    assert rows == {
        "lightroom": (3, 77), "photo_extras": (1, 5), "video_other": (1, 300), "audio": (1, 50),
        "video_no_camera": (1, 200),
    }  # a tmp folder is not a mount point: no disk_used


def test_summarize_splits_photography_from_the_rest() -> None:
    rows = {"lightroom": (3, 70), "video_no_camera": (1, 200), "video_other": (1, 300), "audio": (2, 50),
            "documents": (0, 0), usage.DISK_USED: (0, 2000)}
    s = usage.summarize(tracked_bytes=1200, tracked_files=10, rows=rows, capacity=5000)
    assert s["photo"]["bytes"] == 1200 - 200 + 70 and s["photo"]["files"] == 10 - 1 + 3
    assert [p["category"] for p in s["non_photo"]["parts"]] == ["video_other", "video_no_camera", "audio"]
    assert s["non_photo"]["bytes"] == 550
    assert s["unaccounted"] == 2000 - 1070 - 550 and s["used"] == 2000 and s["free"] == 3000
    assert usage.summarize(1, 1, {}, 5000) is None


def test_summarize_without_disk_used_has_no_free_or_unaccounted() -> None:
    s = usage.summarize(100, 1, {"audio": (1, 5)}, 5000)
    assert s["unaccounted"] is None and s["free"] is None and s["capacity"] is None
