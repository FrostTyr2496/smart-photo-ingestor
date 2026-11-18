"""Phone locations (sp-2, D-066, D-070, D-071): import an osxphotos JSON export into ``geo_points``.

osxphotos runs outside the engine (``uv tool run``) and writes ``PhotoInfo.asdict()`` rows as a JSON array or JSON
lines. This module reads that file with the stdlib only. Each photo or movie with a location, taken on the user's own
iPhone, iPad or Meta glasses, becomes one geo point keyed on ``(source, source_ref = uuid)``, so re-running the same
file changes nothing. Read-only on the file; writes catalog rows only.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

from photovault.catalog import repo
from photovault.geo import utc_text
from photovault.protocol import Emitter

EXPORT_COMMAND = (
    "uv tool run --from 'osxphotos==0.78.1' osxphotos query "
    "--not-screenshot --not-shared --not-syndicated --location --json > photos.json"
)
SOURCES = ("iphone", "ipad", "glasses")
SKIP_REASONS = ("no_location", "screenshot", "shared", "trash", "other_device", "bad_date")
_PROGRESS_EVERY = 5000


class NotOsxphotosJson(ValueError):
    """The file is not an osxphotos JSON export."""


def _not_osxphotos(why: str) -> NotOsxphotosJson:
    return NotOsxphotosJson(f"not an osxphotos JSON export ({why}). Create one with: {EXPORT_COMMAND}")


def _rows(path: Path) -> Iterator[Any]:
    """Rows of a JSON array, or of JSON lines (one object per line)."""
    with open(path, "r", encoding="utf-8") as fh:
        head = ""
        while not head:
            chunk = fh.read(1)
            if not chunk:
                raise _not_osxphotos("the file is empty")
            head = chunk.strip()
        fh.seek(0)
        if head == "[":
            try:
                data = json.load(fh)
            except json.JSONDecodeError as exc:
                raise _not_osxphotos(f"invalid JSON: {exc}") from exc
            yield from data
            return
        for number, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError as exc:
                raise _not_osxphotos(f"line {number} is not JSON") from exc


def device_source(row: dict) -> str:
    """``iphone``, ``ipad``, ``glasses`` or ``other_device`` from the row's EXIF make and model."""
    exif = row.get("exif_info") or {}
    make = (exif.get("camera_make") or "").strip()
    model = (exif.get("camera_model") or "").strip()
    if make == "Meta AI" or "Meta" in model:
        return "glasses"
    if model.startswith("iPad") and make in ("Apple", ""):
        return "ipad"
    if "iPhone" in model and make in ("Apple", ""):
        return "iphone"
    return "other_device"


def _offset_text(delta: timedelta) -> str:
    minutes = round(delta.total_seconds() / 60)
    sign = "+" if minutes >= 0 else "-"
    hours, mins = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}:{mins:02d}"


def parse_when(row: dict) -> tuple[datetime, str | None] | None:
    """``(aware datetime, '+HH:MM' or None)`` from ``date`` (and ``tzoffset`` when the date has no offset)."""
    raw = row.get("date")
    tzoffset = row.get("tzoffset")
    if isinstance(tzoffset, bool) or not isinstance(tzoffset, (int, float)):
        tzoffset = None
    if not isinstance(raw, str):
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if when.tzinfo is None:
        if tzoffset is None:
            return None
        when = when.replace(tzinfo=timezone(timedelta(seconds=tzoffset)))
    elif when.utcoffset() is None:
        return None
    return when, _offset_text(when.utcoffset())


def _place_fields(row: dict) -> tuple[str | None, str | None, str | None, str | None]:
    place = row.get("place") or {}
    address = place.get("address") or {}
    names = place.get("names") or {}

    def first(addr_key: str, names_key: str) -> str | None:
        value = address.get(addr_key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        listed = names.get(names_key)
        if isinstance(listed, list) and listed and isinstance(listed[0], str) and listed[0].strip():
            return listed[0].strip()
        return None

    name = place.get("name")
    name = name.strip() if isinstance(name, str) and name.strip() else None
    return name, first("city", "city"), first("state_province", "state_province"), first("country", "country")


def import_phone(conn: sqlite3.Connection, path: Path, emit: Emitter | None = None) -> dict:
    """Upsert the kept rows of an osxphotos export. Returns the counts (see the spec's Result line)."""
    counts: dict[str, Any] = {"seen": 0, "kept": 0, "inserted": 0, "updated": 0, "unchanged": 0,
                              "skipped": {r: 0 for r in SKIP_REASONS}, "kept_by_source": {s: 0 for s in SOURCES}}
    skipped = counts["skipped"]
    with repo.transaction(conn):
        for row in _rows(Path(path)):
            if not isinstance(row, dict) or ("uuid" not in row and counts["seen"] == 0):
                raise _not_osxphotos("rows are not PhotoInfo objects with a uuid")
            counts["seen"] += 1
            if emit is not None and counts["seen"] % _PROGRESS_EVERY == 0:
                emit.emit("progress", phase="phone", current=counts["seen"], message="")
            lat, lon = row.get("latitude"), row.get("longitude")
            if (isinstance(lat, bool) or isinstance(lon, bool)
                    or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float))):
                skipped["no_location"] += 1
            elif row.get("screenshot") or row.get("screen_recording"):
                skipped["screenshot"] += 1
            elif row.get("shared") or row.get("syndicated"):
                skipped["shared"] += 1
            elif row.get("intrash"):
                skipped["trash"] += 1
            elif (source := device_source(row)) == "other_device" or not row.get("uuid"):
                skipped["other_device"] += 1
            elif (parsed := parse_when(row)) is None:
                skipped["bad_date"] += 1
            else:
                when, local_offset = parsed
                name, city, state, country = _place_fields(row)
                outcome = repo.upsert_geo_point(
                    conn, utc_time=utc_text(when), lat=float(lat), lon=float(lon), alt=None, source=source,
                    source_ref=str(row["uuid"]), place_name=name, city=city, state=state, country=country,
                    accuracy_m=None, local_offset=local_offset, kind="video" if row.get("ismovie") else "photo")
                counts["kept"] += 1
                counts["kept_by_source"][source] += 1
                counts[outcome] += 1
    return counts
