"""Hashing: known vectors, partial-hash window behaviour and identical-file equality."""

import hashlib
import random
from pathlib import Path

import pytest

from photovault.hashing import CHUNK, partial_sha256, sha256_file

KIB = 1024
LARGE_SIZE = 1024 * KIB  # 1 MiB, well above the 192 KiB whole-file threshold
# Windows for LARGE_SIZE (system-design §3): first 64 KiB, middle 64 KiB at size//2 - 32 KiB, last 64 KiB.
WINDOWS = [
    (0, 64 * KIB),
    (LARGE_SIZE // 2 - 32 * KIB, 64 * KIB),
    (LARGE_SIZE - 64 * KIB, 64 * KIB),
]
OUTSIDE_OFFSETS = [300 * KIB, 700 * KIB]  # between the first and middle window, and between middle and last


def _random_bytes(seed: int, size: int) -> bytes:
    return random.Random(seed).randbytes(size)


def _flip(data: bytes, offset: int) -> bytes:
    mutated = bytearray(data)
    mutated[offset] ^= 0xFF
    return bytes(mutated)


def test_sha256_file_empty_known_vector(tmp_path: Path) -> None:
    path = tmp_path / "empty.bin"
    path.write_bytes(b"")
    assert sha256_file(path) == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


def test_sha256_file_abc_known_vector(tmp_path: Path) -> None:
    path = tmp_path / "abc.bin"
    path.write_bytes(b"abc")
    assert sha256_file(path) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert sha256_file(path) == hashlib.sha256(b"abc").hexdigest()


def test_sha256_file_spans_multiple_chunks(tmp_path: Path) -> None:
    data = _random_bytes(1, CHUNK + 12345)
    path = tmp_path / "multi.bin"
    path.write_bytes(data)
    assert sha256_file(path) == hashlib.sha256(data).hexdigest()


def test_partial_small_file_matches_documented_layout(tmp_path: Path) -> None:
    data = b"hello small file"
    path = tmp_path / "small.bin"
    path.write_bytes(data)
    expected = hashlib.sha256(b"pv1" + len(data).to_bytes(8, "big") + data).hexdigest()
    assert partial_sha256(path) == expected


def test_partial_large_file_matches_documented_layout(tmp_path: Path) -> None:
    data = _random_bytes(2, LARGE_SIZE)
    path = tmp_path / "large.bin"
    path.write_bytes(data)
    expected = hashlib.sha256(b"pv1" + LARGE_SIZE.to_bytes(8, "big"))
    for offset, length in WINDOWS:
        expected.update(data[offset : offset + length])
    assert partial_sha256(path) == expected.hexdigest()


@pytest.mark.parametrize("offset", [0, 40 * KIB, 500 * KIB, LARGE_SIZE - 1])
def test_partial_changes_when_byte_inside_a_window_changes(tmp_path: Path, offset: int) -> None:
    data = _random_bytes(3, LARGE_SIZE)
    original = tmp_path / "original.bin"
    changed = tmp_path / "changed.bin"
    original.write_bytes(data)
    changed.write_bytes(_flip(data, offset))
    assert partial_sha256(original) != partial_sha256(changed)


@pytest.mark.parametrize("offset", OUTSIDE_OFFSETS)
def test_partial_unchanged_when_byte_outside_every_window_changes(tmp_path: Path, offset: int) -> None:
    data = _random_bytes(4, LARGE_SIZE)
    original = tmp_path / "original.bin"
    changed = tmp_path / "changed.bin"
    original.write_bytes(data)
    changed.write_bytes(_flip(data, offset))
    assert partial_sha256(original) == partial_sha256(changed)
    # The full hash does see the change, so the partial is only a candidate filter.
    assert sha256_file(original) != sha256_file(changed)


def test_identical_files_have_identical_hashes(tmp_path: Path) -> None:
    data = _random_bytes(5, LARGE_SIZE + 777)
    first = tmp_path / "a" / "photo.bin"
    second = tmp_path / "b" / "copy.bin"
    first.parent.mkdir()
    second.parent.mkdir()
    first.write_bytes(data)
    second.write_bytes(data)
    assert sha256_file(first) == sha256_file(second)
    assert partial_sha256(first) == partial_sha256(second)
