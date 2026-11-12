"""eval-1: the four sequencing approaches on synthetic brackets and bursts, plus the redacted survey fixture.

Unit tests use synthetic frames and the committed fixture. The `live` test reads the registered mounts
(read-only, through the live harness) and writes local-only files under `docs/private/`.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

from photovault.sequence_eval import (
    APPROACHES,
    Scene,
    bracket_step,
    dedupe,
    label,
    load_frames,
    save_frames,
    score,
    summary,
)
from photovault.sequences import detect_runs, load_fixture

FIXTURE = Path(__file__).parent / "fixtures" / "meta" / "cards.jsonl.gz"
PRIVATE = Path(__file__).resolve().parent.parent / "docs" / "private"
FRAMES_CACHE = PRIVATE / "sequence_frames.jsonl.gz"
TRUTH_CACHE = PRIVATE / "sequence_truth.jsonl.gz"
REPORT = PRIVATE / "SEQUENCE_EVAL.md"


def cases() -> dict[str, Scene]:
    return {
        "bracket3_pair": Scene().bracket(3).wait(20).bracket(3),
        "bracket5_pair": Scene().bracket(5).wait(15).bracket(5),
        "bracket3_two_ev_steps": Scene().bracket(3, step=2.0).wait(15).bracket(3, step=2.0),
        "bracket_inside_run": Scene().run(10).bracket(3, base=30.0, fps_idle=2).run(10),
        "long_burst_with_hiccup": Scene().burst(120, fps=7, hiccup_at=60),
        "burst_into_burst_new_settings": Scene().burst(40).burst(30, iso=800),
        "bracket_into_burst": Scene().bracket(3).burst(30),
        "burst_bracket_single": Scene().burst(20).bracket(5).wait(30).single(),
        "whole_second_timestamps": Scene(subsec=False).burst(30, fps=5).wait(60).burst(30, fps=5),
    }


# What each approach gets exactly right (same cut points and the same kind labels). This is the eval-1 result
# on synthetic data, written down so a change to any approach shows up as a diff.
EXPECTED_EXACT: dict[str, set[str]] = {
    "bracket3_pair": {"2-exposure", "3-burst", "4-search"},
    "bracket5_pair": {"2-exposure", "3-burst", "4-search"},
    "bracket3_two_ev_steps": {"2-exposure", "3-burst", "4-search"},
    "bracket_inside_run": {"2-exposure", "4-search"},
    "long_burst_with_hiccup": {"1-flat", "2-exposure", "3-burst", "4-search"},
    "burst_into_burst_new_settings": {"2-exposure", "3-burst", "4-search"},
    "bracket_into_burst": {"2-exposure", "4-search"},
    "burst_bracket_single": {"4-search"},
    "whole_second_timestamps": {"3-burst", "4-search"},
}


@pytest.mark.parametrize("name", list(EXPECTED_EXACT))
def test_synthetic_matrix(name: str) -> None:
    scene = cases()[name]
    exact = {a for a, fn in APPROACHES.items() if score(fn(scene.frames), scene.truth).exact}
    assert exact == EXPECTED_EXACT[name]


def test_search_is_exact_on_every_synthetic_case() -> None:
    for name, scene in cases().items():
        got = APPROACHES["4-search"](scene.frames)
        assert score(got, scene.truth).exact, name


def test_baseline_cannot_split_what_the_gap_rule_does_not_see() -> None:
    scene = cases()["bracket_inside_run"]
    segs = APPROACHES["1-flat"](scene.frames)
    assert [len(s.frames) for s in segs] == [23]  # one run, the bracket is swallowed


# --- shared pieces -------------------------------------------------------------------------------------------


def test_bracket_step_order_independent_and_steps() -> None:
    three = Scene().bracket(3).frames
    five = Scene().bracket(5).frames
    two_ev = Scene().bracket(3, step=2.0).frames
    assert bracket_step(three) == 1.0
    assert bracket_step(five) == 1.0
    assert bracket_step(two_ev) == 2.0
    assert bracket_step(list(reversed(three))) is None or bracket_step(list(reversed(three))) == 1.0


def test_bracket_rejects_constant_exposure_and_wrong_size() -> None:
    assert bracket_step(Scene().burst(3).frames) is None
    assert bracket_step(Scene().burst(4).frames) is None
    assert bracket_step(Scene().bracket(5).frames[:4]) is None


def test_third_of_a_stop_bracket_is_not_recognised_by_default() -> None:
    frames = Scene().bracket(3, step=1 / 3).frames
    assert bracket_step(frames) is None
    assert bracket_step(frames, ev_steps=(1 / 3,)) == pytest.approx(1 / 3)


def test_label() -> None:
    assert label(Scene().single().frames) == "single"
    assert label(Scene().bracket(3).frames) == "bracket"
    assert label(Scene().burst(8).frames) == "burst"
    assert label(Scene().run(6).frames) == "run"


@pytest.mark.parametrize("approach", list(APPROACHES))
def test_every_approach_partitions_the_frames(approach: str) -> None:
    for scene in cases().values():
        segs = APPROACHES[approach](scene.frames)
        flat = [f.shutter for s in segs for f in s.frames]
        assert flat == [f.shutter for f in scene.frames]
        assert all(s.frames for s in segs)


def test_bodies_never_mix() -> None:
    a = Scene(body="SN-A").burst(10).frames
    b = Scene(body="SN-B").burst(10).frames
    for fn in APPROACHES.values():
        segs = fn(a + b)
        assert {s.body for s in segs} == {"SN-A", "SN-B"}
        assert all(len({f.body for f in s.frames}) == 1 for s in segs)


def test_shutter_gap_always_cuts() -> None:
    scene = Scene().burst(10)
    scene.shutter += 5  # 5 frames deleted in camera
    scene.burst(10)
    for fn in APPROACHES.values():
        assert len(fn(scene.frames)) >= 2


# --- the redacted survey fixture -----------------------------------------------------------------------------


@pytest.fixture(scope="module")
def survey_frames():
    return dedupe(load_fixture(FIXTURE))


@pytest.mark.parametrize("approach", list(APPROACHES))
def test_fixture_partition(approach: str, survey_frames) -> None:
    segs = APPROACHES[approach](survey_frames)
    assert sum(len(s.frames) for s in segs) == len(survey_frames)
    assert summary(segs)["segments"] == len(segs)


def test_each_survey_run_stays_in_one_segment_for_the_flat_baseline(survey_frames) -> None:
    segs = APPROACHES["1-flat"](survey_frames)
    where = {(s.body, f.shutter): i for i, s in enumerate(segs) for f in s.frames}
    for run in detect_runs(survey_frames):
        assert len({where[(run.body, f.shutter)] for f in run.frames}) == 1


@pytest.mark.parametrize("approach", ["2-exposure", "3-burst", "4-search"])
def test_survey_night_runs_are_not_shredded(approach: str, survey_frames) -> None:
    """The 19-frame night run (30 s frames) must not be cut into pieces by any approach."""
    segs = APPROACHES[approach](survey_frames)
    where = {(s.body, f.shutter): i for i, s in enumerate(segs) for f in s.frames}
    longest = max(detect_runs(survey_frames), key=lambda r: len(r.frames))
    pieces = {where[(longest.body, f.shutter)] for f in longest.frames}
    assert len(pieces) <= 2, f"{approach} cut the longest survey run into {len(pieces)} pieces"


def test_frame_cache_round_trip(tmp_path: Path, survey_frames) -> None:
    path = tmp_path / "frames.jsonl.gz"
    save_frames(survey_frames, path)
    back = load_frames(path)
    assert back == survey_frames


# --- live: real frames from the registered mounts (read-only) ----------------------------------------------


@pytest.mark.live
def test_live_collect_frames_and_compare(live_sources) -> None:
    """Reads raw metadata of every mounted source, caches the frames locally and writes SEQUENCE_EVAL.md.

    Everything written is under `docs/private/` (gitignored). The mounts are only read.
    """
    from photovault.live_harness import iter_entries
    from photovault.live_survey import _frame
    from photovault.metadata import read_metadata

    frames = []
    for source in live_sources:
        if not source.available:
            continue
        raws = [e for e in iter_entries(source) if e.kind == "raw"]
        result = read_metadata([e.abs_path for e in raws])
        by_path = {e.abs_path: e for e in raws}
        frames += [f for p, m in result.meta.items() if (f := _frame(m, by_path[p])) is not None]
    if not frames:
        pytest.skip("no mounted source produced frames")
    PRIVATE.mkdir(exist_ok=True)
    save_frames(frames, FRAMES_CACHE)
    assert load_frames(FRAMES_CACHE)
    write_report(dedupe(frames))
    assert REPORT.exists()


@pytest.mark.live
def test_live_collect_camera_truth(live_sources) -> None:
    """Reads only the shooting-mode and bracket-value tags of every mounted raw file; caches them locally."""
    from photovault.live_harness import iter_entries

    truth: dict[str, dict] = {}
    for source in live_sources:
        if source.available:
            truth.update(read_camera_truth([e for e in iter_entries(source) if e.kind == "raw"]))
    if not truth:
        pytest.skip("no mounted source produced truth")
    PRIVATE.mkdir(exist_ok=True)
    save_truth(truth, TRUTH_CACHE)
    if FRAMES_CACHE.exists():
        write_report(dedupe(load_frames(FRAMES_CACHE)))


def test_report_from_cached_frames() -> None:
    """Rebuilds the local report from the frame cache without touching any mount (skips if no cache yet)."""
    if not FRAMES_CACHE.exists():
        pytest.skip("no cached frames; run `uv run pytest -m live tests/test_sequence_eval.py` once")
    write_report(dedupe(load_frames(FRAMES_CACHE)))
    assert REPORT.exists()


def read_camera_truth(entries) -> dict[str, dict]:
    """What the camera itself recorded (Nikon maker notes): the shooting-mode flags and the bracket value.

    Read-only `exiftool` through PyExifTool. Keyed by the entry's `rel_path`.
    """
    from exiftool import ExifToolHelper

    from photovault.metadata import _find_exiftool

    tags = ["Nikon:ShootingMode", "Nikon:ExposureBracketValue"]
    out: dict[str, dict] = {}
    paths = {str(e.abs_path): e.rel_path for e in entries}
    keys = list(paths)
    with ExifToolHelper(executable=_find_exiftool(), check_execute=False, common_args=["-G"]) as et:  # text, not -n
        for i in range(0, len(keys), 400):
            for row in et.get_tags(keys[i : i + 400], tags=tags):
                mode = str(row.get("MakerNotes:ShootingMode", row.get("Nikon:ShootingMode", "")) or "")
                value = row.get("MakerNotes:ExposureBracketValue", row.get("Nikon:ExposureBracketValue"))
                out[paths.get(row.get("SourceFile"), row.get("SourceFile"))] = {"mode": mode, "bracket_value": value}
    return out


def save_truth(truth: dict[str, dict], path: Path) -> None:
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for rel, row in truth.items():
            fh.write(json.dumps({"file": rel, **row}) + "\n")


def load_truth(path: Path) -> dict[str, dict]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return {r["file"]: r for r in map(json.loads, fh)}


def bracket_agreement(segments, truth: dict[str, dict]) -> dict[str, float]:
    """Frame level: is a frame in a `bracket` segment when the camera says it was bracketing?"""
    tp = fp = fn = tn = 0
    for seg in segments:
        said = seg.kind == "bracket"
        for f in seg.frames:
            t = "Exposure Bracketing" in truth.get(f.file, {}).get("mode", "")
            tp, fp, fn, tn = tp + (said and t), fp + (said and not t), fn + (t and not said), tn + (not said and not t)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn, "precision": round(precision, 3), "recall": round(recall, 3)}


def write_report(frames) -> None:
    import time

    lines = ["# eval-1: sequencing approaches on real frames (local only)\n"]
    lines.append(f"{len(frames)} unique frames (body, shutter, capture time); generated {time.strftime('%Y-%m-%d %H:%M')}.\n")
    results = {}
    for name, fn in APPROACHES.items():
        t0 = time.monotonic()
        results[name] = fn(frames)
        s = summary(results[name])
        lines.append(f"- **{name}**: {s['segments']} segments {s['by_kind']}, median size {s['median_size']}, "
                     f"largest {s['largest']} ({time.monotonic() - t0:.1f} s)")
    ref = detect_runs(frames)
    lines.append(f"- reference `detect_runs` (seq-1): {len(ref)} runs, lengths {sorted((len(r.frames) for r in ref), reverse=True)[:12]}\n")

    if TRUTH_CACHE.exists():
        truth = load_truth(TRUTH_CACHE)
        from collections import Counter

        modes = Counter(m for r in truth.values() for m in [x.strip() for x in r["mode"].split(",") if x.strip()])
        lines.append(f"\n## Camera shooting-mode flags (Nikon maker notes, {len(truth)} frames)\n{dict(modes.most_common())}\n")
        lines.append("### Bracket detection against the camera's own flag (frame level)")
        for name, segs in results.items():
            lines.append(f"- **{name}**: {bracket_agreement(segs, truth)}")
        lines.append("")
    brackets = [s for s in results["4-search"] if s.kind == "bracket"]
    lines.append(f"## Brackets found by search: {len(brackets)}")
    sizes: dict[int, int] = {}
    for s in brackets:
        sizes[len(s.frames)] = sizes.get(len(s.frames), 0) + 1
    lines.append(f"sizes: {dict(sorted(sizes.items()))}\n")
    for s in brackets[:12]:
        lines.append(f"- {s.frames[0].file} ({len(s.frames)} frames, exposures "
                     f"{[round(f.exposure_s, 4) for f in s.frames]})")

    lines.append("\n## Where the approaches disagree with the baseline (first 15 cuts that differ)\n")
    base = {(s.body, s.frames[0].shutter) for s in results["1-flat"][1:]}
    for name in ("2-exposure", "3-burst", "4-search"):
        extra = sorted({(s.body, s.frames[0].shutter, s.frames[0].file) for s in results[name][1:]
                        if (s.body, s.frames[0].shutter) not in base})
        lines.append(f"### {name}: {len(extra)} cuts the baseline does not make")
        lines += [f"- before {f}" for _, _, f in extra[:15]]
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
