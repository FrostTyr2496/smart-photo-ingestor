"""Run and burst detection (seq-1), checked against the redacted survey fixture and synthetic frames."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from photovault.sequences import FrameMeta, detect_bursts, detect_runs, load_fixture

FIXTURE = Path(__file__).parent / "fixtures" / "meta" / "cards.jsonl.gz"
T0 = datetime(2001, 1, 1, 1, 0, 0)


def frame(shutter: int, offset_s: float, exposure_s: float = 10.0, body: str = "SN-A") -> FrameMeta:
    return FrameMeta(
        body=body,
        shutter=shutter,
        dto=T0 + timedelta(seconds=offset_s),
        subsec="00",
        exposure_s=exposure_s,
        iso=3200,
        fnumber=2.8,
        focal_mm=24.0,
        file=f"DSC_{shutter:04d}.NEF",
        folder="100TEST",
    )


def spans(runs):
    return [(r.body, r.first_shutter, r.last_shutter) for r in runs]


@pytest.fixture(scope="module")
def card1() -> list[FrameMeta]:
    return load_fixture(FIXTURE, card="CARD1")


@pytest.fixture(scope="module")
def card2() -> list[FrameMeta]:
    return load_fixture(FIXTURE, card="CARD2")


# --- fixture (survey numbers) -------------------------------------------------------------------------------


def test_load_fixture_nef_only(card1, card2):
    everything = load_fixture(FIXTURE)
    assert everything and all(f.file.upper().endswith(".NEF") for f in everything)
    assert len(everything) == len(card1) + len(card2)
    assert {f.body for f in everything} == {"SN-A", "SN-B"}
    assert all(isinstance(f.dto, datetime) for f in everything)


def test_night_run_with_framing_shot_interruption(card1):
    runs = [r for r in detect_runs(card1) if r.body == "SN-A" and r.first_shutter == 17396]
    assert len(runs) == 1
    run = runs[0]
    assert run.shutters == list(range(17396, 17415))
    assert [f.shutter for f in run.interruptions] == [17415]
    assert run.interruptions[0].exposure_s == 0.5


def test_survey_runs_card1(card1):
    assert spans(detect_runs(card1)) == [
        ("SN-A", 14052, 14056),
        ("SN-A", 17396, 17414),
        ("SN-A", 17423, 17427),
    ]


def test_survey_runs_card2(card2):
    assert spans(detect_runs(card2)) == [("SN-A", 17372, 17377), ("SN-A", 17384, 17393)]


def test_runs_have_consecutive_shutters_and_long_exposures(card1, card2):
    for run in detect_runs(card1) + detect_runs(card2):
        assert run.shutters == list(range(run.first_shutter, run.last_shutter + 1))
        assert all(f.exposure_s >= 1.0 for f in run.frames)
        assert all(f.exposure_s < 1.0 for f in run.interruptions)


def test_same_second_groups_card1(card1):
    assert len(detect_bursts(card1)) == 54


def test_same_second_groups_card2(card2):
    assert len(detect_bursts(card2)) == 494


def test_bursts_share_body_and_second(card2):
    for burst in detect_bursts(card2)[:50]:
        assert len(burst.frames) >= 3
        assert {f.body for f in burst.frames} == {burst.body}
        assert {f.dto for f in burst.frames} == {burst.dto}


# --- synthetic -----------------------------------------------------------------------------------------------


def test_run_needs_min_frames():
    frames = [frame(100 + i, i * 20) for i in range(4)]
    assert detect_runs(frames) == []
    assert spans(detect_runs(frames, min_frames=4)) == [("SN-A", 100, 103)]


def test_short_exposures_are_not_a_run():
    frames = [frame(100 + i, i * 2, exposure_s=0.25) for i in range(10)]
    assert detect_runs(frames) == []


def test_non_consecutive_shutter_counts_split_runs():
    first = [frame(100 + i, i * 20) for i in range(5)]
    second = [frame(110 + i, 200 + i * 20) for i in range(5)]
    assert spans(detect_runs(first + second)) == [("SN-A", 100, 104), ("SN-A", 110, 114)]


def test_interruption_in_the_middle_is_tolerated():
    frames = [frame(100 + i, i * 20) for i in range(3)]
    frames.append(frame(103, 70, exposure_s=0.5))
    frames += [frame(104 + i, 90 + i * 20) for i in range(3)]
    (run,) = detect_runs(frames)
    assert run.shutters == [100, 101, 102, 104, 105, 106]
    assert [f.shutter for f in run.interruptions] == [103]


def test_two_outliers_break_the_run_by_default():
    frames = [frame(100 + i, i * 20) for i in range(5)]
    frames += [frame(105, 100, 0.5), frame(106, 105, 0.5)]
    frames += [frame(107 + i, 120 + i * 20) for i in range(5)]
    assert spans(detect_runs(frames)) == [("SN-A", 100, 104), ("SN-A", 107, 111)]
    (merged,) = detect_runs(frames, max_outliers=2)
    assert merged.first_shutter == 100 and merged.last_shutter == 111
    assert [f.shutter for f in merged.interruptions] == [105, 106]


def test_large_gap_splits_run():
    frames = [frame(100 + i, i * 20) for i in range(5)]
    frames += [frame(105 + i, 1000 + i * 20) for i in range(5)]
    assert spans(detect_runs(frames)) == [("SN-A", 100, 104), ("SN-A", 105, 109)]


def test_gap_scales_with_long_exposures():
    frames = [frame(100 + i, i * 400, exposure_s=300.0) for i in range(5)]
    assert spans(detect_runs(frames)) == [("SN-A", 100, 104)]


def test_runs_are_per_body_and_input_order_is_irrelevant():
    a = [frame(100 + i, i * 20, body="SN-A") for i in range(5)]
    b = [frame(100 + i, i * 20, body="SN-B") for i in range(5)]
    mixed = [f for pair in zip(b, a) for f in pair]
    assert spans(detect_runs(mixed)) == [("SN-A", 100, 104), ("SN-B", 100, 104)]
    assert spans(detect_runs(reversed(mixed))) == spans(detect_runs(mixed))


def test_burst_groups_same_second_per_body():
    frames = [frame(1, 0.1, 0.01), frame(2, 0.4, 0.01), frame(3, 0.8, 0.01), frame(4, 1.2, 0.01)]
    frames += [frame(10, 0.2, 0.01, body="SN-B"), frame(11, 0.5, 0.01, body="SN-B")]
    (burst,) = detect_bursts(frames)
    assert burst.body == "SN-A" and burst.dto == T0
    assert [f.shutter for f in burst.frames] == [1, 2, 3]
    assert len(detect_bursts(frames, min_frames=2)) == 2
