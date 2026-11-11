"""Preview luminance: synthetic JPEGs, the exiftool fallback order (monkeypatched), and a realfiles check."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from photovault import preview
from photovault.preview import (
    extract_preview,
    is_dark,
    is_flat,
    luma_of_jpeg,
    luma_stats,
    preview_luma,
)

CORPUS = Path(__file__).parent.parent / "testdata" / "corpus"


def _jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def _solid(value: int, size: tuple[int, int] = (300, 200)) -> bytes:
    return _jpeg(Image.new("RGB", size, (value, value, value)))


def _gradient() -> bytes:
    img = Image.new("L", (256, 64))
    img.putdata([x for _ in range(64) for x in range(256)])
    return _jpeg(img.convert("RGB"))


def test_black_is_near_zero():
    assert luma_of_jpeg(_solid(0)) < 2


def test_white_is_near_255():
    assert luma_of_jpeg(_solid(255)) > 253


def test_gradient_is_mid_with_high_variance():
    mean, stddev = luma_stats(_gradient())
    assert 110 < mean < 145
    assert stddev > 50


def test_threshold_helpers():
    assert is_dark(luma_of_jpeg(_solid(0)))
    assert not is_dark(luma_of_jpeg(_solid(40)))
    mean, std = luma_stats(_solid(180))
    assert is_flat(mean, std)
    assert not is_flat(*luma_stats(_gradient()))  # mid luma but high variance
    assert not is_flat(*luma_stats(_solid(30)))  # even but too dark


def test_corrupt_bytes_raise_value_error():
    with pytest.raises(ValueError):
        luma_of_jpeg(b"this is not a jpeg")
    with pytest.raises(ValueError):
        luma_of_jpeg(b"")
    with pytest.raises(ValueError):
        luma_of_jpeg(_solid(100)[:50])  # truncated header


class _FakeRun:
    """Stands in for subprocess.run: maps the requested tag to stdout bytes and records the calls."""

    def __init__(self, outputs: dict[str, bytes], returncode: int = 0):
        self.outputs = outputs
        self.returncode = returncode
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        tag = next(a[1:] for a in cmd if a in ("-PreviewImage", "-JpgFromRaw"))
        return subprocess.CompletedProcess(cmd, self.returncode, stdout=self.outputs.get(tag, b""), stderr=b"")


@pytest.fixture
def fake_exiftool(monkeypatch):
    monkeypatch.setattr(preview, "_find_exiftool", lambda: "/fake/exiftool")


def test_extract_uses_preview_image_first(monkeypatch, fake_exiftool, tmp_path):
    first, second = _solid(10), _solid(200)
    fake = _FakeRun({"PreviewImage": first, "JpgFromRaw": second})
    monkeypatch.setattr(preview.subprocess, "run", fake)
    assert extract_preview(tmp_path / "a.NEF") == first
    assert len(fake.calls) == 1
    assert fake.calls[0][:3] == ["/fake/exiftool", "-b", "-PreviewImage"]


def test_extract_falls_back_to_jpg_from_raw(monkeypatch, fake_exiftool, tmp_path):
    second = _solid(200)
    fake = _FakeRun({"PreviewImage": b"", "JpgFromRaw": second})
    monkeypatch.setattr(preview.subprocess, "run", fake)
    assert extract_preview(tmp_path / "a.NEF") == second
    assert [c[2] for c in fake.calls] == ["-PreviewImage", "-JpgFromRaw"]


def test_extract_returns_none_when_no_preview(monkeypatch, fake_exiftool, tmp_path):
    fake = _FakeRun({})
    monkeypatch.setattr(preview.subprocess, "run", fake)
    assert extract_preview(tmp_path / "a.NEF") is None
    assert len(fake.calls) == 2
    monkeypatch.setattr(preview.subprocess, "run", _FakeRun({"PreviewImage": b"not a jpeg"}))
    assert extract_preview(tmp_path / "a.NEF") is None  # output without a JPEG header is ignored


def test_extract_is_read_only_and_survives_failures(monkeypatch, fake_exiftool, tmp_path):
    fake = _FakeRun({})
    monkeypatch.setattr(preview.subprocess, "run", fake)
    extract_preview(tmp_path / "a.NEF")
    for call in fake.calls:
        assert not any(a.startswith(("-overwrite", "-delete", "-tagsFromFile", "-all=")) for a in call)
        assert not any("=" in a for a in call[1:-1])  # no tag assignments

    def boom(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 1)

    monkeypatch.setattr(preview.subprocess, "run", boom)
    assert extract_preview(tmp_path / "a.NEF") is None


def test_extract_none_when_exiftool_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(preview, "_find_exiftool", lambda: None)
    assert extract_preview(tmp_path / "a.NEF") is None
    assert preview_luma(tmp_path / "a.NEF") is None


def test_preview_luma_end_to_end_and_corrupt(monkeypatch, fake_exiftool, tmp_path):
    monkeypatch.setattr(preview.subprocess, "run", _FakeRun({"PreviewImage": _solid(255)}))
    assert preview_luma(tmp_path / "a.NEF") > 253
    # A JPEG header followed by garbage: extract accepts it, luma fails, preview_luma returns None.
    monkeypatch.setattr(preview.subprocess, "run", _FakeRun({"PreviewImage": b"\xff\xd8garbage"}))
    assert preview_luma(tmp_path / "a.NEF") is None


@pytest.mark.realfiles
def test_real_nef_previews_have_plausible_luma():
    nefs = sorted(CORPUS.glob("**/*.NEF")) if CORPUS.exists() else []
    if not nefs:
        pytest.skip("no NEF files under testdata/corpus")
    if preview._find_exiftool() is None:
        pytest.skip("exiftool binary not installed")
    lumas = [preview_luma(p) for p in nefs[:3]]
    assert any(v is not None for v in lumas), "no preview could be read from any sampled NEF"
    for v in lumas:
        assert v is None or 0.0 <= v <= 255.0
