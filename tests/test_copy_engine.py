from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

import photovault.copy_engine as ce
from photovault.copy_engine import CopyError, copy_verified


def _src(tmp_path: Path, data: bytes = b"photo" * 300_000) -> Path:
    src = tmp_path / "card" / "DSC_0001.NEF"
    src.parent.mkdir(parents=True)
    src.write_bytes(data)
    os.utime(src, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    return src


def test_round_trip(tmp_path: Path) -> None:
    src = _src(tmp_path)
    dst = tmp_path / "work" / "a" / "X.NEF"
    res = copy_verified(src, dst)
    assert dst.read_bytes() == src.read_bytes()
    assert res.sha256 == hashlib.sha256(src.read_bytes()).hexdigest()
    assert dst.stat().st_mtime_ns == src.stat().st_mtime_ns
    assert res.sync_method in ("F_FULLFSYNC", "fsync")
    assert not list(dst.parent.glob(".*pvpartial"))


def test_refuses_existing_dst(tmp_path: Path) -> None:
    src = _src(tmp_path)
    dst = tmp_path / "X.NEF"
    dst.write_bytes(b"other")
    with pytest.raises(FileExistsError):
        copy_verified(src, dst)
    assert dst.read_bytes() == b"other"


def test_failure_mid_write_leaves_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _src(tmp_path, os.urandom(20 * 1024 * 1024))
    dst = tmp_path / "w" / "X.NEF"
    real_write, calls = os.write, []

    def boom(fd, data):  # noqa: ANN001
        calls.append(1)
        if len(calls) > 1:
            raise OSError("disk yanked")
        return real_write(fd, data)

    monkeypatch.setattr(ce.os, "write", boom)
    with pytest.raises(OSError):
        copy_verified(src, dst)
    assert not dst.exists() and not list(dst.parent.iterdir())


def test_readback_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = _src(tmp_path)
    dst = tmp_path / "w" / "X.NEF"
    monkeypatch.setattr(ce, "_hash_uncached", lambda p: "0" * 64)
    with pytest.raises(CopyError, match="mismatch"):
        copy_verified(src, dst)
    assert not dst.exists() and not list(dst.parent.iterdir())


def test_no_xattrs_copied(tmp_path: Path) -> None:
    src = _src(tmp_path)
    subprocess.run(["xattr", "-w", "com.example.test", "1", str(src)], check=True)
    assert "com.example.test" in subprocess.run(["xattr", str(src)], capture_output=True, text=True).stdout
    dst = tmp_path / "w" / "X.NEF"
    copy_verified(src, dst)
    assert subprocess.run(["xattr", str(dst)], capture_output=True, text=True).stdout.strip() == ""


def test_source_is_unchanged(tmp_path: Path) -> None:
    src = _src(tmp_path)
    before = (src.read_bytes(), src.stat().st_mtime_ns)
    copy_verified(src, tmp_path / "w" / "X.NEF")
    assert (src.read_bytes(), src.stat().st_mtime_ns) == before
