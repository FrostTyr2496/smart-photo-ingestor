from __future__ import annotations

from pathlib import Path

import pytest

from photovault.scan import classify, walk


def _write(root: Path, rel: str, data: bytes = b"x") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def card(tmp_path: Path) -> Path:
    root = tmp_path / "card"
    root.mkdir()
    _write(root, "DCIM/101NZ6_2/DSC_0001.NEF")
    _write(root, "DCIM/101NZ6_2/DSC_0001.JPG")
    _write(root, "DCIM/101NZ6_2/._DSC_0001.NEF")
    _write(root, "DCIM/101NZ6_2/DSC_0002.NEF", b"")  # zero-byte
    _write(root, ".Trashes/x.JPG")
    _write(root, "System Volume Information/x")
    _write(root, "MISC/THM/100/DJI_0001.THM")
    _write(root, "NIKON/Z_6_2/SENDLST1.BIN")
    _write(root, "DSC_0850-Edit.tif")
    _write(root, "IMG_0001.HEIC")
    _write(root, "IMG_0001.AAE")
    _write(root, "IMG_0001.MOV")
    _write(root, "GX010020.MP4")
    _write(root, "GX010020.LRV")
    return root


DEFAULT_YIELDS = [
    ("DCIM/101NZ6_2/DSC_0001.JPG", "jpeg"),
    ("DCIM/101NZ6_2/DSC_0001.NEF", "raw"),
    ("DSC_0850-Edit.tif", "derivative"),
    ("GX010020.LRV", "proxy"),
    ("GX010020.MP4", "video"),
    ("IMG_0001.AAE", "sidecar"),
    ("IMG_0001.HEIC", "heic"),
    ("IMG_0001.MOV", "video"),
]

IGNORED_YIELDS = [
    ("MISC/THM/100/DJI_0001.THM", "ignored"),
    ("NIKON/Z_6_2/SENDLST1.BIN", "ignored"),
]


def _pairs(root: Path, **kwargs: bool) -> list[tuple[str, str]]:
    return [(e.rel_path, e.kind) for e in walk(root, **kwargs)]


def test_walk_default_skips_ignored_dotnames_zero_byte_and_skipped_dirs(card: Path) -> None:
    assert _pairs(card) == DEFAULT_YIELDS


def test_include_ignored_adds_only_ignored_files(card: Path) -> None:
    assert _pairs(card, include_ignored=True) == sorted(
        DEFAULT_YIELDS + IGNORED_YIELDS, key=lambda pair: pair[0]
    )


def test_entries_carry_abs_path_size_and_mtime(card: Path) -> None:
    entry = next(e for e in walk(card) if e.rel_path == "IMG_0001.HEIC")
    assert entry.abs_path == card / "IMG_0001.HEIC"
    assert entry.size == 1
    assert entry.mtime_ns == (card / "IMG_0001.HEIC").stat().st_mtime_ns


def test_walk_is_read_only(card: Path) -> None:
    def snapshot() -> dict[str, tuple[int, int]]:
        return {
            p.relative_to(card).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in card.rglob("*")
            if p.is_file()
        }

    before = snapshot()
    list(walk(card, include_ignored=True))
    assert snapshot() == before


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("DSC_0001.NEF", "raw"),
        ("DSC_0001.cr3", "raw"),
        ("a.JPG", "jpeg"),
        ("a.jpeg", "jpeg"),
        ("a.HEIF", "heic"),
        ("a.MOV", "video"),
        ("a.mp4", "video"),
        ("a.tif", "derivative"),
        ("a.PSD", "derivative"),
        ("DSC_0001-Edit.JPG", "derivative"),
        ("DSC_0001-HDR.jpg", "derivative"),
        ("DSC_0001-Pano.NEF", "derivative"),
        ("DSC_0001-Enhanced-NR.jpg", "derivative"),
        ("a.xmp", "sidecar"),
        ("a.srt", "sidecar"),
        ("a.AAE", "sidecar"),
        ("a.LRV", "proxy"),
        ("a.lrf", "proxy"),
        ("a.THM", "ignored"),
        ("a.bin", "ignored"),
        ("noext", "ignored"),
    ],
)
def test_classify(name: str, kind: str) -> None:
    assert classify(name) == kind
