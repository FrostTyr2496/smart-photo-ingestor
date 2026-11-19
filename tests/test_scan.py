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
        ("a.ACR", "sidecar"),
        ("a.LRV", "proxy"),
        ("a.lrf", "proxy"),
        ("a.THM", "ignored"),
        ("a.bin", "ignored"),
        ("noext", "ignored"),
    ],
)
def test_classify(name: str, kind: str) -> None:
    assert classify(name) == kind


def test_walk_skips_windows_system_folders(tmp_path: Path) -> None:
    """An NTFS drive's system folders are never descended; macOS cannot even open `$RECYCLE.BIN` (EINVAL)."""
    root = tmp_path / "ntfs"
    _write(root, "Photos/DSC_0001.NEF")
    for rel in ("$RECYCLE.BIN/S-1-5-21/x.JPG", "$Recycle.Bin/x.JPG", "system volume information/x.JPG",
                "$WinREAgent/x.JPG", "Config.Msi/x.JPG", "Photos/$RECYCLE.BIN/x.JPG"):
        _write(root, rel)
    assert [e.rel_path for e in walk(root)] == ["Photos/DSC_0001.NEF"]


def test_walk_skips_synology_folders(tmp_path: Path) -> None:
    root = tmp_path / "nas"
    _write(root, "Music/Album/@eaDir/Cover.jpg/SYNOPHOTO_THUMB_M.jpg")
    _write(root, "#recycle/old.JPG")
    _write(root, "#snapshot/x.JPG")
    _write(root, "Photos/DSC_0001.JPG")
    assert [e.rel_path for e in walk(root)] == ["Photos/DSC_0001.JPG"]


def test_walk_does_not_trust_the_directory_entry_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Paragon's NTFS driver labels folders as regular files in readdir; the walk must still descend them."""
    import os as _os

    _write(tmp_path, "Photos/Nikon/DSC_0001.NEF")
    _write(tmp_path, "top.JPG")
    real_scandir = _os.scandir

    class Lying:
        def __init__(self, entry):
            self._e = entry
            self.name, self.path = entry.name, entry.path

        def is_dir(self, *, follow_symlinks=True):
            return False

        def is_file(self, *, follow_symlinks=True):
            return True

    class Wrap:
        def __init__(self, path):
            self._it = real_scandir(path)

        def __enter__(self):
            return (Lying(e) for e in self._it)

        def __exit__(self, *exc):
            self._it.close()

        def __iter__(self):
            return (Lying(e) for e in self._it)

    monkeypatch.setattr("photovault.scan.os.scandir", Wrap)
    assert [e.rel_path for e in walk(tmp_path)] == ["Photos/Nikon/DSC_0001.NEF", "top.JPG"]


def test_walk_raises_on_an_unreadable_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write(tmp_path, "Photos/DSC_0001.JPG")

    def boom(path):
        raise OSError(22, "Invalid argument", str(path))

    monkeypatch.setattr("photovault.scan.os.scandir", boom)
    with pytest.raises(OSError):
        list(walk(tmp_path))
