"""Naming, shoot day and Windows safety (ing-5). See system-design §6, D-002, D-004, D-005."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import PurePosixPath

from photovault.config import Config
from photovault.groups import GroupPlan, Member, derivative_suffix

RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
MAX_COMPONENT = 120


class NameCollision(ValueError):
    """Two planned paths are equal case-insensitively (after NFC)."""


def shoot_date(capture: datetime, boundary_hour: int = 12) -> date:
    """The shoot day: times before `boundary_hour` belong to the previous day (a night stays in one folder)."""
    day = capture.date()
    return day - timedelta(days=1) if capture.hour < boundary_hour else day


def windows_safe(name: str) -> str:
    out = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name)
    out = out.rstrip(" .")
    stem = out.split(".")[0].upper()
    if stem in RESERVED:
        out = out[: len(stem)] + "_" + out[len(stem) :]
    if len(out) > MAX_COMPONENT:
        suffix = PurePosixPath(out).suffix if len(PurePosixPath(out).suffix) <= 10 else ""
        out = out[: MAX_COMPONENT - len(suffix)].rstrip(" .") + suffix
    return out or "_"


def body_nickname(cfg: Config, model: str | None) -> str:
    """The `[bodies]` nickname for a Model; unknown (rented, borrowed) bodies get the model without spaces."""
    if not model:
        return "UNKNOWN"
    return cfg.bodies.get(model) or windows_safe(model).replace(" ", "")


def canonical_stem(group: GroupPlan, body: str, template: str) -> str:
    cap = group.capture_time or datetime(1970, 1, 1)
    subsec = (str(group.subsec or "") + "00")[:2] if group.subsec else "00"
    if group.shutter_count is not None:
        ident = f"{group.shutter_count:07d}"
    else:
        ident = re.sub(r"[_\-\s]", "", group.original_stem) or "X"
    stem = template.format(
        num=group.original_num or 0,
        date=cap.strftime("%Y%m%d"),
        time=cap.strftime("%H%M%S"),
        subsec=subsec,
        body=body,
        ident=ident,
    )
    if group.primary.entry.kind == "derivative":
        stem += derivative_suffix(group.primary.entry)
    return windows_safe(stem)


def member_filename(stem: str, member: Member, group: GroupPlan) -> str:
    """The file name for one member: derivative suffix kept, camera-original extensions uppercased."""
    entry = member.entry
    ext = PurePosixPath(entry.rel_path).suffix
    name_stem = stem
    if entry.kind == "derivative" and member is not group.primary:
        name_stem += derivative_suffix(entry)
        return windows_safe(name_stem + ext)
    if entry.kind == "derivative":
        return windows_safe(name_stem + ext)
    return windows_safe(name_stem + ext.upper())


def dest_dir(cfg: Config, shoot: date, shoot_name: str | None, body: str) -> PurePosixPath:
    day = shoot.strftime("%Y%m%d")
    folder = f"{day} - {shoot_name.strip()}" if shoot_name and shoot_name.strip() else day
    return PurePosixPath(cfg.ingest.working_subdir, shoot.strftime("%Y"), windows_safe(folder), windows_safe(body))


def check_unique(rel_paths: Iterable[str]) -> None:
    seen: dict[str, str] = {}
    for rel in rel_paths:
        key = unicodedata.normalize("NFC", rel).casefold()
        if key in seen:
            raise NameCollision(f"planned paths collide: {seen[key]!r} and {rel!r}")
        seen[key] = rel
