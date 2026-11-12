"""Candidate sequence segmenters for eval-1 (an evaluation harness, not production code).

Four ways to cut one body's frames (in shutter order) into sequences, scored on the same synthetic cases and
compared on real frames. Metadata only (vault-rules 9). Nothing here changes `detect_runs` or the §3
signatures; the output of eval-1 is a recommendation. Spec: `docs/specs/readiness/tasks.md` (eval-1).

Approaches, all `fn(frames) -> list[Segment]`:
  1. `segment_flat`      the seq-1 gap rule applied to every frame (the baseline, D-023).
  2. `segment_exposure`  flat, then carve out EV-step brackets and cut where the exposure steps and stays.
  3. `segment_burst`     flat, then cut where the shooting tempo changes (burst vs not) or a burst's settings change.
  4. `segment_search`    optimal partition by dynamic programming over an explicit cost.
"""

from __future__ import annotations

import gzip
import json
import math
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Iterable, Sequence

from photovault.sequences import FrameMeta, _by_body

BRACKET_SIZES = (9, 7, 5, 3)  # tried largest first
KINDS = ("single", "bracket", "burst", "run", "other")


@dataclass(frozen=True)
class Segment:
    body: str
    frames: tuple[FrameMeta, ...]
    kind: str

    @property
    def span(self) -> tuple[str, int, int]:
        return (self.body, self.frames[0].shutter, self.frames[-1].shutter)


# --- shared helpers --------------------------------------------------------------------------------------


def frame_time(f: FrameMeta) -> float:
    """Capture time in seconds, with the sub-second part when the file has one."""
    base = f.dto.timestamp()
    if f.subsec and f.subsec.isdigit():
        base += float("0." + f.subsec)
    return base


def idle_s(prev: FrameMeta, nxt: FrameMeta) -> float:
    """Seconds between the end of `prev`'s exposure and the start of `nxt`.

    Never below 0.02 s, or below 1 s when either frame lacks a sub-second field (whole-second timestamps cannot
    tell 0.1 s from 0.9 s, so everything under a second counts as "about a second").
    """
    floor = 0.02 if (prev.subsec and nxt.subsec) else 1.0
    return max(frame_time(nxt) - frame_time(prev) - prev.exposure_s, floor)


def ev_of(f: FrameMeta) -> float:
    """Exposure setting in stops: bigger means more light gathered. Aperture and ISO are included."""
    n = f.fnumber or 1.0
    iso = f.iso or 100
    return math.log2(f.exposure_s * iso / (n * n))


def same_settings(a: FrameMeta, b: FrameMeta) -> bool:
    return (a.iso, a.fnumber, a.focal_mm) == (b.iso, b.fnumber, b.focal_mm)


def _gap_ok(prev: FrameMeta, nxt: FrameMeta, floor_s: float = 150.0) -> bool:
    gap = frame_time(nxt) - frame_time(prev)
    return 0 <= gap <= prev.exposure_s + max(floor_s, prev.exposure_s)


def bracket_step(frames: Sequence[FrameMeta], ev_steps: Sequence[float] = (1.0, 2.0), tol: float = 0.35) -> float | None:
    """The EV step if `frames` are an exposure bracket, else None.

    A bracket has 3, 5, 7 or 9 frames whose exposure settings, sorted, form an even progression of one of
    `ev_steps` (order does not matter, so metered-first and ascending brackets both match). All frames share
    the focal length, and each follows the previous within 10 s idle (camera auto-bracketing or fast manual).
    """
    n = len(frames)
    if n not in BRACKET_SIZES:
        return None
    if len({f.focal_mm for f in frames}) != 1 or len({f.fnumber for f in frames}) != 1:
        return None
    if any(idle_s(a, b) > 10.0 for a, b in zip(frames, frames[1:])):
        return None
    evs = sorted(ev_of(f) for f in frames)
    diffs = [b - a for a, b in zip(evs, evs[1:])]
    for step in ev_steps:
        if all(abs(d - step) <= tol for d in diffs):
            return step
    return None


def label(frames: Sequence[FrameMeta]) -> str:
    """One shared labeller, so the approaches differ only in where they cut."""
    n = len(frames)
    if n == 1:
        return "single"
    if bracket_step(frames) is not None:
        return "bracket"
    idles = [idle_s(a, b) for a, b in zip(frames, frames[1:])]
    if n >= 3 and statistics.median(idles) <= 1.0:
        return "burst"
    if n >= 5 and statistics.median(f.exposure_s for f in frames) >= 1.0:
        return "run"
    return "other"


def _finish(body: str, blocks: Iterable[Sequence[FrameMeta]]) -> list[Segment]:
    return [Segment(body, tuple(b), label(b)) for b in blocks if b]


def _per_body(
    frames: Iterable[FrameMeta], one_body: Callable[[list[FrameMeta]], list[list[FrameMeta]]]
) -> list[Segment]:
    out: list[Segment] = []
    for body, group in sorted(_by_body(frames).items()):
        out.extend(_finish(body, one_body(group)))
    return out


def _flat_blocks(group: list[FrameMeta]) -> list[list[FrameMeta]]:
    blocks: list[list[FrameMeta]] = []
    for f in group:
        if blocks and f.shutter == blocks[-1][-1].shutter + 1 and _gap_ok(blocks[-1][-1], f):
            blocks[-1].append(f)
        else:
            blocks.append([f])
    return blocks


def _split_at(block: list[FrameMeta], cuts: set[int]) -> list[list[FrameMeta]]:
    """Cut `block` before each index in `cuts`."""
    out, start = [], 0
    for i in sorted(cuts):
        if 0 < i < len(block):
            out.append(block[start:i])
            start = i
    out.append(block[start:])
    return out


# --- 1. flat floor (baseline) ----------------------------------------------------------------------------


def segment_flat(frames: Iterable[FrameMeta]) -> list[Segment]:
    return _per_body(frames, _flat_blocks)


# --- 2. exposure-aware -----------------------------------------------------------------------------------


def _carve_brackets(block: list[FrameMeta]) -> list[list[FrameMeta]]:
    out: list[list[FrameMeta]] = []
    rest: list[FrameMeta] = []
    i = 0
    while i < len(block):
        for size in BRACKET_SIZES:
            if i + size <= len(block) and bracket_step(block[i : i + size]) is not None:
                if rest:
                    out.append(rest)
                    rest = []
                out.append(block[i : i + size])
                i += size
                break
        else:
            rest.append(block[i])
            i += 1
    if rest:
        out.append(rest)
    return out


def segment_exposure(frames: Iterable[FrameMeta], *, step_ev: float = 0.9, tol: float = 0.35) -> list[Segment]:
    """Flat blocks, then brackets carved out, then cuts where the exposure steps by `step_ev` or more and the
    new exposure holds for the next frame (a step that does not hold is a framing shot, not a new sequence)."""

    def one(group: list[FrameMeta]) -> list[list[FrameMeta]]:
        out: list[list[FrameMeta]] = []
        for block in _flat_blocks(group):
            for piece in _carve_brackets(block):
                if bracket_step(piece) is not None:
                    out.append(piece)
                    continue
                cuts = {
                    i
                    for i in range(1, len(piece) - 1)
                    if abs(ev_of(piece[i]) - ev_of(piece[i - 1])) >= step_ev
                    and abs(ev_of(piece[i + 1]) - ev_of(piece[i])) <= tol
                }
                out.extend(_split_at(piece, cuts))
        return out

    return _per_body(frames, one)


# --- 3. burst-aware chaining -----------------------------------------------------------------------------


def segment_burst(frames: Iterable[FrameMeta], *, burst_idle_s: float = 1.0, ratio: float = 8.0) -> list[Segment]:
    """Flat blocks, then cuts where the tempo changes class (a link with idle <= `burst_idle_s` is fast) or where
    a fast link changes ISO, aperture or focal length. A single slow link between two fast ones (a buffer
    hiccup, idle up to `ratio` times the burst's own idle and under 1.5 s) does not cut."""

    def one(group: list[FrameMeta]) -> list[list[FrameMeta]]:
        out: list[list[FrameMeta]] = []
        for block in _flat_blocks(group):
            idle = [0.0] + [idle_s(a, b) for a, b in zip(block, block[1:])]  # idle[i]: link i-1 -> i
            fast = [v <= burst_idle_s for v in idle]
            fast_idles = [v for v in idle[1:] if v <= burst_idle_s]
            ref = statistics.median(fast_idles) if fast_idles else 1.0
            cuts: set[int] = set()
            for i in range(1, len(block)):
                if fast[i]:
                    if not same_settings(block[i - 1], block[i]):
                        cuts.add(i)
                    continue
                before = i >= 2 and fast[i - 1]
                after = i + 1 < len(block) and fast[i + 1]
                hiccup = before and after and idle[i] <= min(1.5, ratio * ref)
                if (before or after) and not hiccup:
                    cuts.add(i)
            out.extend(_split_at(block, cuts))
        return out

    return _per_body(frames, one)


# --- 4. search-based segmentation ------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchCosts:
    """Hand-set weights. Lower total cost = a split the data contradicts less. Not learned from data."""

    segment: float = 4.0  # opening a segment
    bracket: float = 1.0  # opening a segment that is exactly a bracket
    tempo: float = 1.0  # per unit of squared log-idle deviation inside a segment
    settings: float = 6.0  # an ISO, aperture or focal change inside a segment
    exposure: float = 2.0  # per stop of exposure change between neighbours inside a non-bracket segment
    max_len: int = 300


def segment_search(frames: Iterable[FrameMeta], *, costs: SearchCosts = SearchCosts()) -> list[Segment]:
    """Optimal partition (dynamic programming, O(n * max_len)) of each flat block into segments.

    A segment is either a bracket (cost `bracket`, only for an exact motif) or a stream costing
    `segment + tempo * SSE(ln idle) + settings * changes + exposure * sum(|stop change|, capped at 3)`. The
    SSE term is the variance of the start-to-start interval (log scale, so 10 s frames 12 s or 37 s apart differ by
    little, while 0.1 s against 0.5 s does) inside the segment, computed from prefix sums.
    """

    def one(group: list[FrameMeta]) -> list[list[FrameMeta]]:
        out: list[list[FrameMeta]] = []
        for block in _flat_blocks(group):
            n = len(block)
            if n == 1:
                out.append(block)
                continue
            x = [0.0] + [math.log(idle_s(a, b) + a.exposure_s) for a, b in zip(block, block[1:])]  # start-to-start, x[i]: link i-1 -> i
            sw = [0] + [0 if same_settings(a, b) else 1 for a, b in zip(block, block[1:])]
            ex = [0.0] + [min(3.0, abs(ev_of(b) - ev_of(a))) for a, b in zip(block, block[1:])]
            p1, p2, ps, pe = ([0.0] * (n + 1) for _ in range(4))
            for i in range(1, n + 1):  # prefix sums over the per-frame link values (index 0 has no link)
                p1[i], p2[i] = p1[i - 1] + x[i - 1], p2[i - 1] + x[i - 1] ** 2
                ps[i], pe[i] = ps[i - 1] + sw[i - 1], pe[i - 1] + ex[i - 1]

            def stream(i: int, j: int) -> float:
                """Cost of frames i..j-1 as one stream; its links are i+1..j-1."""
                m = j - i - 1
                if m <= 0:
                    return costs.segment
                a, b = i + 1, j  # links a..b-1: prefix[b] - prefix[a]
                s1, s2 = p1[b] - p1[a], p2[b] - p2[a]
                sse = max(0.0, s2 - s1 * s1 / m)
                return (
                    costs.segment
                    + costs.tempo * sse
                    + costs.settings * (ps[b] - ps[a])
                    + costs.exposure * (pe[b] - pe[a])
                )

            best = [0.0] + [math.inf] * n
            prev = [0] * (n + 1)
            for j in range(1, n + 1):
                for i in range(max(0, j - costs.max_len), j):
                    c = stream(i, j)
                    if j - i in BRACKET_SIZES and bracket_step(block[i:j]) is not None:
                        c = min(c, costs.bracket)
                    if best[i] + c < best[j]:
                        best[j], prev[j] = best[i] + c, i
            cuts, j = [], n
            while j > 0:
                cuts.append(prev[j])
                j = prev[j]
            out.extend(_split_at(block, set(cuts)))
        return out

    return _per_body(frames, one)


APPROACHES: dict[str, Callable[[Iterable[FrameMeta]], list[Segment]]] = {
    "1-flat": segment_flat,
    "2-exposure": segment_exposure,
    "3-burst": segment_burst,
    "4-search": segment_search,
}


# --- scoring ---------------------------------------------------------------------------------------------


def boundaries(segments: Sequence[Segment]) -> set[tuple[str, int]]:
    """Cut positions as (body, shutter of the first frame after the cut)."""
    return {(s.body, s.frames[0].shutter) for s in segments[1:]}


@dataclass(frozen=True)
class Score:
    exact: bool  # the partition equals the truth
    precision: float
    recall: float
    kinds_right: int  # truth segments found with the same span and the same kind
    kinds_total: int


def score(found: Sequence[Segment], truth: Sequence[Segment]) -> Score:
    fb, tb = boundaries(found), boundaries(truth)
    tp = len(fb & tb)
    precision = tp / len(fb) if fb else 1.0
    recall = tp / len(tb) if tb else 1.0
    got = {s.span: s.kind for s in found}
    right = sum(1 for s in truth if got.get(s.span) == s.kind)
    return Score(
        exact=[s.span for s in found] == [s.span for s in truth],
        precision=precision,
        recall=recall,
        kinds_right=right,
        kinds_total=len(truth),
    )


# --- synthetic scene builder (used by the tests) -----------------------------------------------------------

T0 = datetime(2001, 1, 1, 12, 0, 0)


class Scene:
    """Builds a frame list and its ground truth, one part at a time. Shutter counts stay consecutive."""

    def __init__(self, body: str = "SN-A", subsec: bool = True) -> None:
        self.body, self.use_subsec = body, subsec
        self.t = 0.0
        self.shutter = 1000
        self.frames: list[FrameMeta] = []
        self.truth: list[Segment] = []

    def _add(self, exposure: float, iso: int = 100, f: float = 8.0, focal: float = 24.0) -> FrameMeta:
        ms = round(self.t * 1000)  # integer milliseconds, so float error cannot move a frame by a second
        whole = T0 + timedelta(seconds=ms // 1000)
        sub = f"{(ms % 1000) // 10:02d}" if self.use_subsec else None
        fm = FrameMeta(
            self.body, self.shutter, whole, sub, exposure, iso, f, focal,
            f"DSC_{self.shutter:04d}.NEF", "100TEST",
        )
        self.shutter += 1
        self.frames.append(fm)
        return fm

    def _close(self, start: int, kind: str) -> None:
        self.truth.append(Segment(self.body, tuple(self.frames[start:]), kind))

    def wait(self, seconds: float) -> "Scene":
        self.t += seconds
        return self

    def bracket(self, n: int = 3, step: float = 1.0, base: float = 1 / 125, fps_idle: float = 0.4, **kw: float) -> "Scene":
        start = len(self.frames)
        offsets = [0.0] + [s * k * step for k in range(1, n // 2 + 1) for s in (-1, 1)]  # metered, then -, +
        for off in offsets:
            exposure = base * 2**off
            self._add(exposure, **kw)  # type: ignore[arg-type]
            self.t += exposure + fps_idle
        self._close(start, "bracket")
        return self

    def burst(self, n: int, fps: float = 10.0, hiccup_at: int | None = None, exposure: float = 1 / 1000, **kw: float) -> "Scene":
        start = len(self.frames)
        for i in range(n):
            self._add(exposure, **kw)  # type: ignore[arg-type]
            self.t += 1 / fps + (0.4 if hiccup_at == i + 1 else 0.0)
        self._close(start, "burst")
        return self

    def run(self, n: int, exposure: float = 30.0, idle: float = 4.0, **kw: float) -> "Scene":
        start = len(self.frames)
        for _ in range(n):
            self._add(exposure, **kw)  # type: ignore[arg-type]
            self.t += exposure + idle
        self._close(start, "run")
        return self

    def single(self, exposure: float = 1 / 250, **kw: float) -> "Scene":
        start = len(self.frames)
        self._add(exposure, **kw)  # type: ignore[arg-type]
        self.t += exposure + 0.5
        self._close(start, "single")
        return self


# --- frame cache (real data stays local) -----------------------------------------------------------------


def save_frames(frames: Iterable[FrameMeta], path: Path) -> None:
    """Write frames as gzip JSON lines. The file holds real file names: keep it under `docs/private/`."""
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for f in frames:
            fh.write(
                json.dumps(
                    {"body": f.body, "shutter": f.shutter, "dto": f.dto.isoformat(), "subsec": f.subsec,
                     "exposure_s": f.exposure_s, "iso": f.iso, "fnumber": f.fnumber, "focal_mm": f.focal_mm,
                     "file": f.file, "folder": f.folder}
                )
                + "\n"
            )


def load_frames(path: Path) -> list[FrameMeta]:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return [
            FrameMeta(r["body"], r["shutter"], datetime.fromisoformat(r["dto"]), r["subsec"], r["exposure_s"],
                      r["iso"], r["fnumber"], r["focal_mm"], r["file"], r["folder"])
            for r in map(json.loads, fh)
        ]


# --- summaries for the real-data comparison ----------------------------------------------------------------


def summary(segments: Sequence[Segment]) -> dict[str, object]:
    """Counts by kind and the size spread, for a side-by-side table."""
    by_kind: dict[str, int] = {k: 0 for k in KINDS}
    for s in segments:
        by_kind[s.kind] += 1
    sizes = sorted(len(s.frames) for s in segments)
    return {
        "segments": len(segments),
        "by_kind": by_kind,
        "median_size": statistics.median(sizes) if sizes else 0,
        "largest": sizes[-5:][::-1],
    }


def dedupe(frames: Iterable[FrameMeta]) -> list[FrameMeta]:
    """One frame per (body, shutter, capture time): copies of the same photo must not look like extra frames."""
    unique: dict[tuple[str, int, datetime], FrameMeta] = {}
    for f in frames:
        unique.setdefault((f.body, f.shutter, f.dto), f)
    return list(unique.values())
