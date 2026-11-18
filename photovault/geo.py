"""Geotag suggestions (ui-3): match a frame to GPS fixes from other sources by capture time.

Times: a group's UTC is its capture time (naive, or with `capture_offset`, else the `default_offset` setting) with
`time_shift_s` added (the corrected clock). Stored and compared times are ISO-8601 UTC at whole seconds with a
`+00:00` suffix, so string range queries in `geo_points` work.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Mapping

from photovault.catalog import repo

HIGH_S = 120
MEDIUM_S = 900
_OFFSET_RE = re.compile(r"^([+-])(\d{2}):?(\d{2})$")


def offset_tz(text: str) -> timezone:
    """'+HH:MM' / '-HH:MM' / '+HHMM' / 'Z' to a fixed timezone."""
    if text in ("Z", "z"):
        return UTC
    match = _OFFSET_RE.match(text.strip())
    if not match:
        raise ValueError(f"bad UTC offset {text!r}; expected +HH:MM")
    sign = 1 if match.group(1) == "+" else -1
    return timezone(sign * timedelta(hours=int(match.group(2)), minutes=int(match.group(3))))


def utc_text(when: datetime) -> str:
    """Canonical stored form: ``YYYY-MM-DDTHH:MM:SS+00:00`` (whole seconds, always UTC)."""
    return when.astimezone(UTC).isoformat(timespec="seconds")


def _field(row: Mapping[str, Any] | sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


def group_utc(row: Mapping[str, Any] | sqlite3.Row, default_offset: str) -> datetime | None:
    """UTC of a capture group, corrected by its ``time_shift_s``; None when there is no capture time.

    Needs ``capture_time``, ``capture_offset`` and (optionally) ``time_shift_s``; a missing shift counts as 0.
    """
    raw = _field(row, "capture_time")
    if not raw:
        return None
    when = datetime.fromisoformat(str(raw))
    if when.tzinfo is None:
        when = when.replace(tzinfo=offset_tz(_field(row, "capture_offset") or default_offset or "+00:00"))
    shift = int(_field(row, "time_shift_s") or 0)
    return when.astimezone(UTC) + timedelta(seconds=shift)


def _confidence(abs_delta_s: float) -> str:
    if abs_delta_s <= HIGH_S:
        return "high"
    if abs_delta_s <= MEDIUM_S:
        return "medium"
    return "low"


def suggest(conn: sqlite3.Connection, group_id: int, *, window_s: int = 1800, limit: int = 3) -> list[dict]:
    """Geotag candidates for one group, nearest in time first.

    Points within ``±window_s`` of the group's UTC, except the group's own camera point
    (``source_ref == "group:<id>"``). When one point lies before the frame and one after (both inside the
    window), the first suggestion is their linear interpolation (source ``interpolated``, delta 0, confidence from
    the wider of the two gaps). Each dict: lat, lon, alt, source, source_ref, delta_s, confidence, and the point's
    place_name, city, state, country (None for an interpolated one).
    """
    row = conn.execute(
        """
        SELECT g.capture_time, g.capture_offset, COALESCE(m.time_shift_s, 0) AS time_shift_s
        FROM capture_groups g LEFT JOIN group_meta m ON m.group_id = g.group_id
        WHERE g.group_id = ?
        """,
        (group_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no capture group {group_id}")
    target = group_utc(row, repo.get_setting(conn, "default_offset", "+00:00"))
    if target is None:
        return []

    window = timedelta(seconds=window_s)
    own = f"group:{group_id}"
    points = []
    for p in repo.geo_points_between(conn, utc_text(target - window), utc_text(target + window)):
        if p["source_ref"] == own:
            continue
        gap = (datetime.fromisoformat(p["utc_time"]) - target).total_seconds()
        points.append({"lat": p["lat"], "lon": p["lon"], "alt": p["alt"], "source": p["source"],
                       "source_ref": p["source_ref"], "delta_s": round(gap), "confidence": _confidence(abs(gap)),
                       "place_name": p["place_name"], "city": p["city"], "state": p["state"],
                       "country": p["country"], "_gap": gap})

    before = [p for p in points if p["_gap"] < 0]
    after = [p for p in points if p["_gap"] > 0]
    interpolated = []
    if before and after:
        lo = max(before, key=lambda p: p["_gap"])  # nearest before the frame
        hi = min(after, key=lambda p: p["_gap"])  # nearest after the frame
        frac = -lo["_gap"] / (hi["_gap"] - lo["_gap"])
        alt = None
        if lo["alt"] is not None and hi["alt"] is not None:
            alt = lo["alt"] + frac * (hi["alt"] - lo["alt"])
        wider = max(-lo["_gap"], hi["_gap"])
        interpolated.append({"lat": lo["lat"] + frac * (hi["lat"] - lo["lat"]),
                             "lon": lo["lon"] + frac * (hi["lon"] - lo["lon"]), "alt": alt,
                             "source": "interpolated", "source_ref": f"{lo['source_ref']}|{hi['source_ref']}",
                             "delta_s": 0, "confidence": _confidence(wider),
                             "place_name": None, "city": None, "state": None, "country": None})

    points.sort(key=lambda p: abs(p["_gap"]))
    for p in points:
        del p["_gap"]
    return (interpolated + points)[:limit]


def places_near(conn: sqlite3.Connection, group_id: int, *, window_s: int = 3600) -> list[dict]:
    """Distinct named places of phone points within ``±window_s`` of a group (sp-3), nearest in time first.

    Only points that carry a place name, city, state or country count. Each dict: place_name, city, state,
    country, a representative lat/lon/alt (the point nearest in time) and that point's delta_s.
    """
    found = suggest(conn, group_id, window_s=window_s, limit=10**6)
    out: dict[tuple, dict] = {}
    for p in found:  # already nearest first
        if p["source"] == "interpolated":
            continue
        key = (p["place_name"], p["city"], p["state"], p["country"])
        if not any(key) or key in out:
            continue
        out[key] = {"place_name": p["place_name"], "city": p["city"], "state": p["state"], "country": p["country"],
                    "lat": p["lat"], "lon": p["lon"], "alt": p["alt"], "delta_s": p["delta_s"]}
    return list(out.values())
