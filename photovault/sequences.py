"""Run and burst detection from metadata only (seq-1).

Pure functions over `FrameMeta` rows: no pixels, no LLM, no network, no ExifTool calls (vault-rules 9). Labels
produced from these groups never decide whether a byte is kept. Design: audit §5.4; spec:
`docs/specs/sequences/tasks.md`.
"""

from __future__ import annotations

import gzip
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class FrameMeta:
    """The metadata of one frame that sequence detection needs."""

    body: str
    shutter: int
    dto: datetime
    subsec: str | None
    exposure_s: float
    iso: int | None
    fnumber: float | None
    focal_mm: float | None
    file: str
    folder: str


@dataclass(frozen=True)
class Run:
    """Frames of one body with consecutive shutter counts and a plausible cadence.

    `interruptions` lists single out-of-rule frames (for example a framing shot) that were skipped. They sit
    inside the run's shutter span or directly after its last frame, and are not in `frames`.
    """

    body: str
    frames: tuple[FrameMeta, ...]
    interruptions: tuple[FrameMeta, ...] = field(default=())

    @property
    def first_shutter(self) -> int:
        return self.frames[0].shutter

    @property
    def last_shutter(self) -> int:
        return self.frames[-1].shutter

    @property
    def shutters(self) -> list[int]:
        return [f.shutter for f in self.frames]


@dataclass(frozen=True)
class Burst:
    """Frames of one body that share a capture-time second (`dto`)."""

    body: str
    dto: datetime
    frames: tuple[FrameMeta, ...]


def load_fixture(path: str | Path, *, card: str | None = None) -> list[FrameMeta]:
    """Load NEF rows of a (redacted) `cards.jsonl.gz` fixture as `FrameMeta`.

    `card` optionally keeps only rows from that card label (for example `"CARD1"`). Rows without a body,
    shutter count, capture time or exposure cannot be sequenced and are skipped.
    """
    frames: list[FrameMeta] = []
    with gzip.open(Path(path), "rt", encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            row = json.loads(line)
            if not str(row["file"]).upper().endswith(".NEF"):
                continue
            if card is not None and row.get("card") != card:
                continue
            if (
                row.get("body") is None
                or row.get("shutter") is None
                or row.get("dto") is None
                or row.get("exposure_s") is None
            ):
                continue
            frames.append(
                FrameMeta(
                    body=row["body"],
                    shutter=int(row["shutter"]),
                    dto=datetime.fromisoformat(row["dto"]),
                    subsec=row.get("subsec"),
                    exposure_s=float(row["exposure_s"]),
                    iso=row.get("iso"),
                    fnumber=row.get("fnumber"),
                    focal_mm=row.get("focal_mm"),
                    file=row["file"],
                    folder=row["folder"],
                )
            )
    return frames


def _by_body(frames: Iterable[FrameMeta]) -> dict[str, list[FrameMeta]]:
    groups: dict[str, list[FrameMeta]] = defaultdict(list)
    for frame in frames:
        groups[frame.body].append(frame)
    for group in groups.values():
        group.sort(key=lambda f: (f.shutter, f.dto))
    return groups


def _gap_ok(prev: FrameMeta, nxt: FrameMeta, floor_s: float) -> bool:
    """True when `nxt` starts within `exposure + max(floor_s, exposure)` of `prev`."""
    gap = (nxt.dto - prev.dto).total_seconds()
    return 0 <= gap <= prev.exposure_s + max(floor_s, prev.exposure_s)


def detect_runs(
    frames: Iterable[FrameMeta],
    *,
    min_frames: int = 5,
    min_exposure_s: float = 1.0,
    max_outliers: int = 1,
    max_gap_s: float = 150.0,
) -> list[Run]:
    """Find runs per body: consecutive shutter counts, long exposures, a plausible cadence.

    A run's frames all have `exposure_s >= min_exposure_s`. Between two adjacent run frames the start-to-start
    gap is at most `exposure + max(max_gap_s, exposure)`. Up to `max_outliers` consecutive frames below
    `min_exposure_s` are tolerated when the run resumes on the next frame within the stricter spec gap of
    `exposure + max(60 s, exposure)`, measured from the last run frame. They go to `Run.interruptions`, as do
    up to `max_outliers` such frames directly after the last run frame. Only runs with at least `min_frames`
    frames are returned, ordered by body then shutter count.

    `max_gap_s` is not in the spec text (which says 60 s). The survey runs (a 30 s frame followed 108 s later
    by the next, 5 s then 2 s frames 138 s apart) need about 150 s, so 150 s is the default; the 60 s figure
    still applies across an interruption, which is what keeps neighbouring survey runs apart.
    """
    runs: list[Run] = []
    for body, group in sorted(_by_body(frames).items()):
        current: list[FrameMeta] = []
        interruptions: list[FrameMeta] = []
        pending: list[FrameMeta] = []

        def close() -> None:
            nonlocal current, interruptions, pending
            if len(current) >= min_frames:
                runs.append(Run(body, tuple(current), tuple(interruptions + pending)))
            current, interruptions, pending = [], [], []

        for frame in group:
            last_shutter = pending[-1].shutter if pending else (current[-1].shutter if current else None)
            consecutive = last_shutter is not None and frame.shutter == last_shutter + 1
            is_long = frame.exposure_s >= min_exposure_s
            if current and consecutive:
                if not is_long:
                    if len(pending) < max_outliers:
                        pending.append(frame)
                        continue
                elif _gap_ok(current[-1], frame, 60.0 if pending else max_gap_s):
                    interruptions.extend(pending)
                    pending = []
                    current.append(frame)
                    continue
            close()
            if is_long:
                current = [frame]
        close()
    return runs


def detect_bursts(frames: Iterable[FrameMeta], *, min_frames: int = 3) -> list[Burst]:
    """Group frames of one body that share a `dto` second; keep groups of at least `min_frames`."""
    bursts: list[Burst] = []
    for body, group in sorted(_by_body(frames).items()):
        seconds: dict[datetime, list[FrameMeta]] = defaultdict(list)
        for frame in group:
            seconds[frame.dto.replace(microsecond=0)].append(frame)
        for dto, members in sorted(seconds.items()):
            if len(members) >= min_frames:
                bursts.append(Burst(body, dto, tuple(members)))
    return bursts
