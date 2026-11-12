"""Read-only survey of one live source (live-1, vault-rules 2 exception D-028).

`survey_source` scans a source, reads metadata for its raw files, runs `detect_runs` and `detect_bursts`,
and hashes a few files for a read-throughput figure. It never writes to the source. The result is plain data;
`render_markdown` turns a list of results into a report for `docs/private/` (gitignored). The report holds
counts, rates and timings only: no serials, volume UUIDs, GPS values or file names.
"""

from __future__ import annotations

import statistics
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from collections.abc import Iterable
from typing import Any, Callable

from photovault.hashing import sha256_file
from photovault.live_harness import LiveSource, iter_entries
from photovault.metadata import read_metadata
from photovault.scan import FileEntry
from photovault.sequences import FrameMeta, detect_bursts, detect_runs
from photovault.volumes import probe

META_FIELDS = (
    "Model",
    "CameraSerialNumber",
    "ShutterCount",
    "DateTimeOriginal",
    "SubSecTimeOriginal",
    "ExposureTime",
    "ISO",
    "FNumber",
    "GPSLatitude",
)
HASH_PROBE_FILES = 3
HASH_PROBE_MIN_BYTES = 1_000_000


@dataclass
class SourceSurvey:
    name: str
    role: str
    mounted: bool
    volume: dict[str, Any] = field(default_factory=dict)
    scan_s: float = 0.0
    kind_counts: dict[str, int] = field(default_factory=dict)
    kind_bytes: dict[str, int] = field(default_factory=dict)
    pair_stems: int = 0
    raw_jpeg_pairs: int = 0
    case_collisions: int = 0
    meta_requested: int = 0
    meta_read: int = 0
    meta_errors: int = 0
    meta_s: float = 0.0
    field_presence: dict[str, int] = field(default_factory=dict)
    models: dict[str, int] = field(default_factory=dict)
    time_sources: dict[str, int] = field(default_factory=dict)
    frames: int = 0
    duplicate_frames: int = 0
    runs: list[dict[str, Any]] = field(default_factory=list)
    interruptions: int = 0
    bursts: int = 0
    longest_burst: int = 0
    hash_mb_s: float | None = None
    notes: list[str] = field(default_factory=list)


def _volume_facts(source: LiveSource) -> dict[str, Any]:
    try:
        p = probe(source.path)
    except (RuntimeError, OSError) as exc:
        return {"error": str(exc)}
    return {
        "fs_type": p.fs_type,
        "capacity_gb": round(p.capacity_bytes / 1e9, 1) if p.capacity_bytes else None,
        "internal": p.internal,
        "looks_like_card": p.looks_like_card,
        "has_marker": p.marker_id is not None,
        "has_volume_uuid": p.da_volume_uuid is not None,
    }


def _frame(meta: dict[str, Any], entry: FileEntry) -> FrameMeta | None:
    body, shutter = meta.get("Model"), meta.get("ShutterCount")
    dto, exposure = meta.get("DateTimeOriginal"), meta.get("ExposureTime")
    if body is None or shutter is None or not isinstance(dto, datetime) or exposure is None:
        return None
    return FrameMeta(
        body=str(body),
        shutter=int(shutter),
        dto=dto,
        subsec=meta.get("SubSecTimeOriginal"),
        exposure_s=float(exposure),
        iso=meta.get("ISO"),
        fnumber=meta.get("FNumber"),
        focal_mm=meta.get("FocalLength"),
        file=entry.rel_path,
        folder=entry.rel_path.rpartition("/")[0],
    )


def _evenly(items: list[FileEntry], cap: int) -> list[FileEntry]:
    if len(items) <= cap:
        return items
    step = len(items) / cap
    return [items[int(i * step)] for i in range(cap)]


def survey_source(
    source: LiveSource,
    *,
    raw_cap: int = 20000,
    other_sample: int = 100,
    progress: Callable[[str], None] | None = None,
) -> SourceSurvey:
    """Survey one source read-only. `raw_cap` bounds the raw files whose metadata is read."""
    say = progress or (lambda _msg: None)
    out = SourceSurvey(name=source.name, role=source.role, mounted=source.available)
    if not source.available:
        out.notes.append("not mounted")
        return out
    out.volume = _volume_facts(source)

    say(f"{source.name}: scanning")
    t0 = time.monotonic()
    entries = list(iter_entries(source))
    out.scan_s = time.monotonic() - t0
    kinds, sizes = Counter(), Counter()
    for e in entries:
        kinds[e.kind] += 1
        sizes[e.kind] += e.size
    out.kind_counts, out.kind_bytes = dict(kinds), dict(sizes)

    stems: dict[str, set[str]] = {}
    for e in entries:
        stems.setdefault(e.rel_path.rsplit(".", 1)[0], set()).add(e.kind)
    out.pair_stems = len(stems)
    out.raw_jpeg_pairs = sum(1 for k in stems.values() if {"raw", "jpeg"} <= k)
    lowered = Counter(e.rel_path.lower() for e in entries)
    out.case_collisions = sum(1 for n in lowered.values() if n > 1)

    raws = [e for e in entries if e.kind == "raw"]
    others = [e for e in entries if e.kind in ("jpeg", "heic", "video")]
    chosen = _evenly(raws, raw_cap) + _evenly(others, other_sample)
    if len(raws) > raw_cap:
        out.notes.append(f"raw metadata sampled: {raw_cap} of {len(raws)}; run detection is not meaningful")
    out.meta_requested = len(chosen)
    say(f"{source.name}: reading metadata for {len(chosen)} files")
    t0 = time.monotonic()
    result = read_metadata([e.abs_path for e in chosen])
    out.meta_s = time.monotonic() - t0
    out.meta_read, out.meta_errors = len(result.meta), len(result.errors)

    presence: Counter[str] = Counter()
    models: Counter[str] = Counter()
    tsrc: Counter[str] = Counter()
    frames: list[FrameMeta] = []
    by_path = {e.abs_path: e for e in chosen}
    for path, meta in result.meta.items():
        for name in META_FIELDS:
            if meta.get(name) is not None:
                presence[name] += 1
        if meta.get("Model"):
            models[str(meta["Model"])] += 1
        if meta.get("CaptureTimeSource"):
            tsrc[meta["CaptureTimeSource"]] += 1
        entry = by_path[path]
        if entry.kind == "raw":
            frame = _frame(meta, entry)
            if frame is not None:
                frames.append(frame)
    out.field_presence, out.models, out.time_sources = dict(presence), dict(models), dict(tsrc)

    out.frames = len(frames)
    seen = Counter((f.body, f.shutter, f.dto) for f in frames)
    out.duplicate_frames = sum(n - 1 for n in seen.values() if n > 1)
    if len(raws) <= raw_cap:
        unique: dict[tuple[str, int, datetime], FrameMeta] = {}
        for f in frames:
            unique.setdefault((f.body, f.shutter, f.dto), f)
        deduped = list(unique.values())
        for run in detect_runs(deduped):
            gaps = [(b.dto - a.dto).total_seconds() for a, b in zip(run.frames, run.frames[1:])]
            out.runs.append(
                {
                    "frames": len(run.frames),
                    "exposure_s": statistics.median(f.exposure_s for f in run.frames),
                    "max_gap_s": max(gaps) if gaps else 0.0,
                    "interruptions": len(run.interruptions),
                }
            )
        out.interruptions = sum(r["interruptions"] for r in out.runs)
        bursts = detect_bursts(deduped)
        out.bursts = len(bursts)
        out.longest_burst = max((len(b.frames) for b in bursts), default=0)

    probes = [e for e in entries if e.size >= HASH_PROBE_MIN_BYTES][:HASH_PROBE_FILES]
    if probes:
        say(f"{source.name}: hash throughput probe on {len(probes)} files")
        t0 = time.monotonic()
        total = 0
        for e in probes:
            sha256_file(e.abs_path)
            total += e.size
        elapsed = time.monotonic() - t0
        out.hash_mb_s = round(total / 1e6 / elapsed, 1) if elapsed > 0 else None
    return out


def _fmt_bytes(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def render_markdown(surveys: list[SourceSurvey], *, when: datetime) -> str:
    """Render the survey as a markdown report (counts and timings only)."""
    lines = [f"# Live survey ({when:%Y-%m-%d %H:%M})", "", "Read-only. Local only (gitignored). No hashes stored, no catalog written.", ""]
    for s in surveys:
        lines += [f"## {s.name} ({s.role})", ""]
        if not s.mounted:
            lines += ["Not mounted.", ""]
            continue
        v = s.volume
        lines.append(f"- Volume: {v}")
        lines.append(f"- Scan: {sum(s.kind_counts.values())} files in {s.scan_s:.1f} s")
        lines.append("- Kinds: " + ", ".join(f"{k} {n} ({_fmt_bytes(s.kind_bytes[k])})" for k, n in sorted(s.kind_counts.items())))
        lines.append(f"- Raw+JPEG pairs by stem: {s.raw_jpeg_pairs} of {s.pair_stems} stems; case-insensitive path collisions: {s.case_collisions}")
        rate = s.meta_read / s.meta_s if s.meta_s else 0
        lines.append(f"- Metadata: {s.meta_read}/{s.meta_requested} read, {s.meta_errors} errors, {s.meta_s:.1f} s ({rate:.0f} files/s)")
        lines.append("- Field presence: " + ", ".join(f"{k} {s.field_presence.get(k, 0)}" for k in META_FIELDS))
        lines.append(f"- Models: {s.models}; capture-time sources: {s.time_sources}")
        lines.append(f"- Sequenceable raw frames: {s.frames}; duplicate (body, shutter, time) frames: {s.duplicate_frames}")
        if s.runs:
            lengths = sorted((r["frames"] for r in s.runs), reverse=True)
            lines.append(f"- Runs (flat 150 s floor): {len(s.runs)}; lengths {lengths[:15]}; max in-run gap {max(r['max_gap_s'] for r in s.runs):.0f} s; interruptions {s.interruptions}")
        else:
            lines.append("- Runs: none detected (or not evaluated)")
        lines.append(f"- Bursts (>=3 frames in one second): {s.bursts}; longest {s.longest_burst}")
        lines.append(f"- Hash read throughput: {s.hash_mb_s} MB/s" if s.hash_mb_s else "- Hash read throughput: not measured")
        for note in s.notes:
            lines.append(f"- Note: {note}")
        lines.append("")
    return "\n".join(lines)


def write_report(
    surveys: list[SourceSurvey], dest: Path, *, when: datetime, source_roots: Iterable[Path]
) -> None:
    """Write the report to `dest`. Refuses a `dest` inside any of `source_roots` (never write to a source)."""
    target = dest.resolve()
    for root in source_roots:
        if target.is_relative_to(root.resolve()):
            raise ValueError(f"refusing to write the report inside source {root}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render_markdown(surveys, when=when), encoding="utf-8")
