"""Capture groups (ing-4): files that belong to one shot (NEF+JPG, sidecars, derivatives, live video).

See system-design §3 (groups) and §8 (kinds and roles).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any

from photovault.scan import FileEntry

DERIVATIVE_SUFFIX = re.compile(r"(-edit|-hdr|-pano|-enhanced-nr)$", re.IGNORECASE)
MEDIA_EXT_IN_STEM = re.compile(r"\.(nef|nrw|dng|cr2|cr3|arw|raf|orf|rw2|jpe?g|heic|heif|mov|mp4)$", re.IGNORECASE)
PRIMARY_ORDER = ("raw", "heic", "jpeg", "video")
FOLDER_HINTS = ("PANORAMA", "HYPERLAPSE")


@dataclass
class Member:
    entry: FileEntry
    role: str


@dataclass
class GroupPlan:
    key: str
    members: list[Member]
    primary: Member
    model: str | None = None
    serial: str | None = None
    shutter_count: int | None = None
    capture_time: datetime | None = None
    subsec: str | None = None
    offset: str | None = None
    time_source: str = "mtime"
    original_stem: str = ""
    original_num: int | None = None
    folder_hint: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def base_stem(entry: FileEntry) -> str:
    """The stem used to group: derivative suffixes and a media extension inside a sidecar stem are stripped."""
    stem = PurePosixPath(entry.rel_path).stem
    stem = MEDIA_EXT_IN_STEM.sub("", stem)
    return DERIVATIVE_SUFFIX.sub("", stem)


def derivative_suffix(entry: FileEntry) -> str:
    """The original `-Edit`-style suffix of a file's stem, or ''."""
    m = DERIVATIVE_SUFFIX.search(PurePosixPath(entry.rel_path).stem)
    return m.group(1) if m else ""


def trailing_num(stem: str) -> int | None:
    m = re.search(r"(\d+)$", stem)
    return int(m.group(1)[-4:]) if m else None


def _role(entry: FileEntry, primary: FileEntry, kinds: set[str]) -> str:
    if entry is primary:
        return "primary"
    ext = PurePosixPath(entry.rel_path).suffix.lower()
    if entry.kind == "sidecar":
        return {".srt": "telemetry", ".aae": "aae"}.get(ext, "sidecar")
    if entry.kind == "video" and kinds & {"heic", "jpeg"}:
        return "live_video"
    if entry.kind in ("jpeg", "derivative", "proxy"):
        return entry.kind
    return "alternate"


def build_groups(entries: list[FileEntry], meta: dict[Path, dict]) -> list[GroupPlan]:
    """Group entries by (parent dir, base stem), pick a primary and take identity from metadata."""
    buckets: dict[tuple[str, str], list[FileEntry]] = {}
    for e in entries:
        parent = str(PurePosixPath(e.rel_path).parent)
        buckets.setdefault((parent, base_stem(e).upper()), []).append(e)

    groups: list[GroupPlan] = []
    for (parent, stem_up), files in sorted(buckets.items()):
        files.sort(key=lambda e: e.rel_path)
        kinds = {e.kind for e in files}
        primary = next((e for k in PRIMARY_ORDER for e in files if e.kind == k), None)
        orphan = False
        if primary is None:
            primary = next((e for e in files if e.kind == "derivative"), None) or files[0]
            orphan = primary.kind == "sidecar"
        members = []
        for e in files:
            role = "orphan_sidecar" if orphan and e is primary else _role(e, primary, kinds)
            members.append(Member(e, role))
        prim_member = next(m for m in members if m.entry is primary)

        def pick(key: str) -> Any:
            for e in [primary] + [f for f in files if f is not primary]:
                value = meta.get(e.abs_path, {}).get(key)
                if value not in (None, ""):
                    return value
            return None

        capture = pick("DateTimeOriginal")
        time_source = pick("CaptureTimeSource") if capture is not None else "mtime"
        if not isinstance(capture, datetime):
            capture = datetime.fromtimestamp(primary.mtime_ns / 1e9)
            time_source = "mtime"
        shutter = pick("ShutterCount")
        hint = None
        parts = PurePosixPath(primary.rel_path).parts
        for i, part in enumerate(parts[:-1]):
            if part.upper() in FOLDER_HINTS:
                hint = "/".join(parts[: i + 1])
        original_stem = base_stem(primary)
        groups.append(
            GroupPlan(
                key=f"{parent}/{stem_up}",
                members=members,
                primary=prim_member,
                model=pick("Model"),
                serial=pick("CameraSerialNumber"),
                shutter_count=int(shutter) if shutter is not None else None,
                capture_time=capture,
                subsec=pick("SubSecTimeOriginal"),
                offset=pick("OffsetTimeOriginal"),
                time_source=str(time_source),
                original_stem=original_stem,
                original_num=trailing_num(original_stem),
                folder_hint=hint,
            )
        )
    return groups
