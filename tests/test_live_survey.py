"""live-1: the read-only survey. The unit tests use tmp_path; the `live` test reads the registered mounts."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

import photovault.live_survey as ls
from photovault.live_harness import LiveSource
from photovault.live_survey import render_markdown, survey_source, write_report
from photovault.metadata import MetadataResult
from photovault.volumes import VolumeProbe

REPORT = Path(__file__).resolve().parent.parent / "docs" / "private" / "LIVE_SURVEY.md"


@pytest.fixture
def fake_card(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> LiveSource:
    root = tmp_path / "card"
    folder = root / "DCIM" / "100TEST"
    folder.mkdir(parents=True)
    for i in range(6):
        (folder / f"DSC_{i:04d}.NEF").write_bytes(b"n" * 2_000_000)
        (folder / f"DSC_{i:04d}.JPG").write_bytes(b"j" * 10)
    (folder / "DSC_0000.nef.xmp").write_bytes(b"x")

    def fake_probe(mount: Path, **_: object) -> VolumeProbe:
        return VolumeProbe(mount, "X", "exfat", None, 1000, False, None, True)

    def fake_read(paths, **_: object) -> MetadataResult:
        meta = {}
        for i, p in enumerate(sorted(q for q in paths if q.suffix == ".NEF")):
            meta[p] = {
                "Model": "TEST BODY",
                "ShutterCount": 100 + i,
                "DateTimeOriginal": datetime(2026, 1, 1, 22, 0, 0) + timedelta(seconds=20 * i),
                "ExposureTime": 10.0,
                "CaptureTimeSource": "exif",
            }
        return MetadataResult(meta=meta, errors={p: "unreadable" for p in paths if p.suffix == ".JPG"})

    monkeypatch.setattr(ls, "probe", fake_probe)
    monkeypatch.setattr(ls, "read_metadata", fake_read)
    return LiveSource("card", root, "CARD")


def test_survey_counts_runs_and_pairs(fake_card: LiveSource) -> None:
    s = survey_source(fake_card)
    assert s.kind_counts["raw"] == 6 and s.kind_counts["jpeg"] == 6
    assert s.raw_jpeg_pairs == 6
    assert s.frames == 6 and s.duplicate_frames == 0
    assert [r["frames"] for r in s.runs] == [6]
    assert s.meta_errors == 6
    assert s.hash_mb_s is not None
    assert s.volume["fs_type"] == "exfat"


def test_survey_of_an_unmounted_source_is_marked(tmp_path: Path) -> None:
    s = survey_source(LiveSource("gone", tmp_path / "nope", "CARD"))
    assert s.mounted is False and "not mounted" in s.notes


def test_report_has_counts_but_no_file_names(fake_card: LiveSource) -> None:
    text = render_markdown([survey_source(fake_card)], when=datetime(2026, 1, 2, 3, 4))
    assert "## card (CARD)" in text and "Runs (flat 150 s floor): 1" in text
    assert "DSC_0000" not in text


def test_report_refuses_to_land_inside_a_source(fake_card: LiveSource) -> None:
    surveys = [survey_source(fake_card)]
    with pytest.raises(ValueError, match="refusing"):
        write_report(surveys, fake_card.path / "report.md", when=datetime.now(), source_roots=[fake_card.path])
    assert not (fake_card.path / "report.md").exists()


def test_report_is_written_outside_the_source(fake_card: LiveSource, tmp_path: Path) -> None:
    dest = tmp_path / "out" / "r.md"
    write_report([survey_source(fake_card)], dest, when=datetime.now(), source_roots=[fake_card.path])
    assert dest.read_text().startswith("# Live survey")


@pytest.mark.live
def test_live_survey_writes_local_report(live_sources: list[LiveSource]) -> None:
    surveys = []
    for source in live_sources:
        if not source.available:
            continue
        s = survey_source(source, progress=lambda m: print(m, flush=True))
        assert s.meta_requested == 0 or s.meta_read + s.meta_errors == s.meta_requested
        surveys.append(s)
    if not surveys:
        pytest.skip("no registered source is mounted")
    write_report(surveys, REPORT, when=datetime.now(), source_roots=[s.path for s in live_sources])
