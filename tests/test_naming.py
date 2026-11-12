from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import pytest

from photovault.config import load_config
from photovault.groups import build_groups
from photovault.naming import (
    NameCollision,
    body_nickname,
    canonical_stem,
    check_unique,
    dest_dir,
    member_filename,
    shoot_date,
    windows_safe,
)
from photovault.scan import FileEntry

TEMPLATE = "M{num:04d}_{date}-{time}-{subsec}_{body}_{ident}"


def _e(rel: str, kind: str) -> FileEntry:
    return FileEntry(rel, Path("/card") / rel, 10, 1_700_000_000_000_000_000, kind)


def test_shoot_date_noon_boundary() -> None:
    assert shoot_date(datetime(2026, 10, 27, 23, 57)) == shoot_date(datetime(2026, 10, 28, 1, 28)) == date(2026, 10, 27)
    assert shoot_date(datetime(2026, 10, 27, 12, 0)) == date(2026, 10, 27)
    assert shoot_date(datetime(2026, 10, 27, 11, 59)) == date(2026, 10, 26)


def test_nikon_and_iphone_stems() -> None:
    nef = _e("DCIM/100/DSC_0781.NEF", "raw")
    g = build_groups([nef], {nef.abs_path: {"DateTimeOriginal": datetime(2026, 10, 27, 18, 14, 42),
                                            "SubSecTimeOriginal": "81", "ShutterCount": 84022,
                                            "CaptureTimeSource": "exif", "Model": "NIKON Z 6"}})[0]
    assert canonical_stem(g, "Z6", TEMPLATE) == "M0781_20261027-181442-81_Z6_0084022"
    heic = _e("DCIM/100APPLE/IMG_4821.HEIC", "heic")
    g2 = build_groups([heic], {heic.abs_path: {"DateTimeOriginal": datetime(2026, 1, 2, 3, 4, 5),
                                               "CaptureTimeSource": "exif"}})[0]
    stem = canonical_stem(g2, "IP14P", TEMPLATE)
    assert stem.startswith("M4821_") and stem.endswith("_IP14P_IMG4821") and "-00_" in stem


def test_derivative_suffix_kept() -> None:
    nef, edit = _e("a/DSC_0850.NEF", "raw"), _e("a/DSC_0850-Edit.tif", "derivative")
    g = build_groups([nef, edit], {nef.abs_path: {"ShutterCount": 1, "DateTimeOriginal": datetime(2026, 1, 1, 13),
                                                  "CaptureTimeSource": "exif"}})[0]
    stem = canonical_stem(g, "Z6", TEMPLATE)
    names = {m.entry.kind: member_filename(stem, m, g) for m in g.members}
    assert names["raw"].endswith(".NEF") and names["derivative"].endswith("-Edit.tif")


def test_windows_safe() -> None:
    assert windows_safe("C:2023 A3/Comet*") == "C_2023 A3_Comet_"
    assert windows_safe("CON") == "CON_"
    assert windows_safe("name. ") == "name"
    assert len(windows_safe("x" * 200)) == 120


def test_dest_dir_and_unknown_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path))
    cfg = load_config()
    assert str(dest_dir(cfg, date(2026, 10, 27), "Lighthouse Moonrise", "Z6")) == \
        "Photography/Imports/2026/20261027 - Lighthouse Moonrise/Z6"
    assert str(dest_dir(cfg, date(2026, 10, 27), None, "Z6")) == "Photography/Imports/2026/20261027/Z6"
    assert body_nickname(cfg, "NIKON Z 8") == "Z8"
    assert body_nickname(cfg, "NIKON Z 9") == "NIKONZ9"  # rented/unknown body: auto nickname


def test_check_unique() -> None:
    with pytest.raises(NameCollision):
        check_unique(["a/X.NEF", "a/x.nef"])
    check_unique(["a/X.NEF", "b/X.NEF"])
