"""The two-time model (sh-0b, D-080; `system-design.md` §5g).

The **instant** of a capture group is `capture_time` minus the camera's offset (`capture_offset`, else the
`default_offset` setting), plus `group_meta.time_shift_s`, plus the accepted `body_clock.correction_s` of its body
whose `[start_utc, end_utc]` range covers the instant before the correction. It is computed, never stored.
Provenance says how trustworthy that is: ``aligned`` (a body clock row applied), ``exif_offset`` (the camera wrote
its offset), ``assumed_offset`` (the default was used). ``gps`` is reserved for cameras whose time came from GPS;
no column records that yet, so it is never returned. A manual ``time_shift_s`` is the separate `user_shift` flag.

The **wall clock** is what a person saw on the day: the instant in the shoot's `local_offset` when the group is in
a shoot that has one, else `capture_time` exactly as the camera wrote it.

This module imports nothing from the rest of the package, so `geo`, `clock` and `repo` can all use it.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Mapping, NamedTuple

_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")

GPS, ALIGNED, EXIF_OFFSET, ASSUMED_OFFSET = "gps", "aligned", "exif_offset", "assumed_offset"


def offset_tz(text: str) -> timezone:
    """'+HH:MM' / '-HH:MM' / '+HHMM' / 'Z' to a fixed timezone."""
    if text in ("Z", "z"):
        return UTC
    match = _OFFSET_RE.match(text.strip())
    if not match:
        raise ValueError(f"bad UTC offset {text!r}; expected +HH:MM")
    sign = 1 if match.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3))))


def _field(row: Mapping[str, Any] | sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


class Instant(NamedTuple):
    when: datetime
    provenance: str
    user_shift: bool
    correction_s: int  # the body_clock correction applied (0 when none)


class BodyClocks:
    """Accepted `body_clock` rows, for lookup by (body_id, instant). Load once per request, never per row."""

    def __init__(self, rows: list[tuple[int, datetime, datetime, int]] | None = None) -> None:
        self._by_body: dict[int, list[tuple[datetime, datetime, int]]] = {}
        for body_id, start, end, corr in rows or []:
            self._by_body.setdefault(body_id, []).append((start, end, corr))
        for lst in self._by_body.values():
            lst.sort(key=lambda r: r[0])

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> "BodyClocks":
        rows = conn.execute(
            "SELECT body_id, start_utc, end_utc, correction_s FROM body_clock WHERE accepted = 1").fetchall()
        return cls([(r["body_id"], datetime.fromisoformat(r["start_utc"]), datetime.fromisoformat(r["end_utc"]),
                     int(r["correction_s"])) for r in rows])

    def __bool__(self) -> bool:
        return bool(self._by_body)

    def correction(self, body_id: int | None, when: datetime) -> int | None:
        """Seconds to add for ``body_id`` at ``when`` (the instant before the correction); None when none covers it.
        Of several covering rows the latest start wins."""
        if body_id is None:
            return None
        for start, end, corr in reversed(self._by_body.get(body_id, ())):
            if start <= when <= end:
                return corr
        return None


def instant_info(row: Mapping[str, Any] | sqlite3.Row, default_offset: str,
                 clocks: BodyClocks | None = None) -> Instant | None:
    """The instant of a group row, with provenance; None without a capture time.

    The row needs ``capture_time`` and may have ``capture_offset``, ``time_shift_s`` and ``body_id``."""
    raw = _field(row, "capture_time")
    if not raw:
        return None
    when = datetime.fromisoformat(str(raw))
    stated = _field(row, "capture_offset")
    if when.tzinfo is None:
        when = when.replace(tzinfo=offset_tz(stated or default_offset or "+00:00"))
    shift = int(_field(row, "time_shift_s") or 0)
    when = when.astimezone(UTC) + timedelta(seconds=shift)
    corr = clocks.correction(_field(row, "body_id"), when) if clocks else None
    if corr is not None:
        when += timedelta(seconds=corr)
        prov = ALIGNED
    else:
        prov = EXIF_OFFSET if stated else ASSUMED_OFFSET
    return Instant(when, prov, shift != 0, corr or 0)


def instant(row: Mapping[str, Any] | sqlite3.Row, *, default_offset: str,
            clocks: BodyClocks | None = None) -> tuple[datetime, str] | None:
    """``(UTC datetime, provenance)`` of a group row, or None without a capture time."""
    info = instant_info(row, default_offset, clocks)
    return (info.when, info.provenance) if info else None


def wall_clock(row: Mapping[str, Any] | sqlite3.Row, when: datetime | None = None) -> str | None:
    """The wall-clock time (naive ISO, whole seconds) of a group row: the instant ``when`` in the shoot's
    ``local_offset`` (the row's ``shoot_local_offset`` or ``local_offset``) when it has one, else ``capture_time`` as
    written. None without a capture time."""
    raw = _field(row, "capture_time")
    if not raw:
        return None
    local = _field(row, "shoot_local_offset") or _field(row, "local_offset")
    if local and when is not None:
        return when.astimezone(offset_tz(local)).replace(tzinfo=None).isoformat(timespec="seconds")
    return str(raw)[:19]


def group_wall_clock(conn: sqlite3.Connection, group_id: int, default_offset: str,
                     clocks: BodyClocks | None = None) -> str | None:
    """`wall_clock` for one group id, reading its shoot's offset from the catalog."""
    row = conn.execute(
        "SELECT g.body_id, g.capture_time, g.capture_offset, COALESCE(m.time_shift_s, 0) AS time_shift_s, "
        "s.local_offset AS shoot_local_offset FROM capture_groups g "
        "LEFT JOIN group_meta m ON m.group_id = g.group_id LEFT JOIN shoots s ON s.shoot_id = g.shoot_id "
        "WHERE g.group_id = ?", (group_id,)).fetchone()
    if row is None:
        return None
    info = instant_info(row, default_offset, clocks if clocks is not None else BodyClocks.load(conn))
    return wall_clock(row, info.when if info else None)
