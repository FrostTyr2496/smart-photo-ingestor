"""Geotag suggestions (ui-3): match a frame to GPS fixes from other sources by capture time.

Times: a group's UTC is its instant (`photovault.timemodel`: capture time less its offset, plus `time_shift_s` and the
accepted body clock correction). Stored and compared times are ISO-8601 UTC at whole seconds with a
`+00:00` suffix, so string range queries in `geo_points` work.
"""

from __future__ import annotations

import bisect
import math
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping

from photovault import timemodel
from photovault.catalog import repo
from photovault.timemodel import BodyClocks, offset_tz  # noqa: F401  (offset_tz is re-exported)

HIGH_S = 120
MEDIUM_S = 900
SESSION_CAP_S = 12 * 3600  # session_of looks this far either side
HINT_S = 48 * 3600  # last / next known place
NEAR_KM = 1.0  # "stayed put" when both ends are this close, or in the same place
FAR_S = 3 * 3600
TIERS = ("high", "medium", "low", "far", "weak")  # best to worst; "hint" sits below them
SCORE = {"high": 1.0, "medium": 0.8, "low": 0.6, "far": 0.4, "weak": 0.2, "hint": 0.1}


def utc_text(when: datetime) -> str:
    """Canonical stored form: ``YYYY-MM-DDTHH:MM:SS+00:00`` (whole seconds, always UTC)."""
    return when.astimezone(UTC).isoformat(timespec="seconds")


def group_utc(row: Mapping[str, Any] | sqlite3.Row, default_offset: str,
              clocks: BodyClocks | None = None) -> datetime | None:
    """The instant of a capture group (UTC); None when there is no capture time. Delegates to `timemodel.instant`.

    Needs ``capture_time``; uses ``capture_offset``, ``time_shift_s`` and, with ``clocks`` (a loaded `BodyClocks`) and
    ``body_id``, the accepted body clock corrections. Missing fields count as absent.
    """
    found = timemodel.instant(row, default_offset=default_offset, clocks=clocks)
    return found[0] if found else None


def _confidence(abs_delta_s: float) -> str:
    if abs_delta_s <= HIGH_S:
        return "high"
    if abs_delta_s <= MEDIUM_S:
        return "medium"
    return "low"


def _tier(abs_s: float, window_s: int, reach_s: int) -> str | None:
    """high <= 2 min, medium <= 15 min, low <= window, far <= 3 h, weak <= reach (D-075); None beyond."""
    if abs_s <= HIGH_S:
        return "high"
    if abs_s <= MEDIUM_S:
        return "medium"
    if abs_s <= window_s:
        return "low"
    if abs_s <= FAR_S:
        return "far"
    return "weak" if abs_s <= reach_s else None


def _shift(tier: str, steps: int) -> str:
    """Move along TIERS (negative = raise, positive = lower), never above medium or below weak."""
    return TIERS[min(max(TIERS.index(tier) + steps, 1), len(TIERS) - 1)]


def session_of(conn: sqlite3.Connection, group_id: int, gap_s: int = 900) -> list[dict]:
    """The photo's session (sp-8): frames of any body chained by corrected-UTC gaps of at most ``gap_s``.

    Capped at +-12 h of the photo. Returns dicts ``{group_id, stem, utc}`` in time order, the photo included
    (empty when it has no capture time). `capture_groups.capture_time` has no index, so the query is a string range
    on the local time with a margin wide enough for any UTC offset and clock shift, then exact in Python.
    """
    default = repo.get_setting(conn, "default_offset", "+00:00")
    clocks = BodyClocks.load(conn)
    row = conn.execute(
        "SELECT g.body_id, g.capture_time, g.capture_offset, COALESCE(m.time_shift_s, 0) AS time_shift_s "
        "FROM capture_groups g LEFT JOIN group_meta m ON m.group_id = g.group_id WHERE g.group_id = ?",
        (group_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no capture group {group_id}")
    target = group_utc(row, default, clocks)
    if target is None:
        return []
    raw = datetime.fromisoformat(str(row["capture_time"])).replace(tzinfo=None)
    margin = timedelta(seconds=SESSION_CAP_S, hours=40)  # offset up to +-14 h, plus shifts up to a day
    lo, hi = (raw - margin).isoformat(timespec="seconds"), (raw + margin).isoformat(timespec="seconds")
    frames = []
    for r in conn.execute(
        "SELECT g.group_id, g.body_id, g.canonical_stem, g.capture_time, g.capture_offset, COALESCE(m.time_shift_s, 0) AS time_shift_s "
        "FROM capture_groups g LEFT JOIN group_meta m ON m.group_id = g.group_id "
        "WHERE g.capture_time >= ? AND g.capture_time <= ?", (lo, hi)):
        when = group_utc(r, default, clocks)
        if when is not None and abs((when - target).total_seconds()) <= SESSION_CAP_S:
            frames.append({"group_id": r["group_id"], "stem": r["canonical_stem"], "utc": when})
    frames.sort(key=lambda f: (f["utc"], f["group_id"]))
    idx = next(i for i, f in enumerate(frames) if f["group_id"] == group_id)
    start = end = idx
    while start > 0 and (frames[start]["utc"] - frames[start - 1]["utc"]).total_seconds() <= gap_s:
        start -= 1
    while end < len(frames) - 1 and (frames[end + 1]["utc"] - frames[end]["utc"]).total_seconds() <= gap_s:
        end += 1
    return frames[start:end + 1]


def _place_key(p: Mapping[str, Any]) -> tuple:
    return (p["place_name"], p["city"], p["state"], p["country"])


def _km(a: Mapping[str, Any], b: Mapping[str, Any]) -> float:
    p1, p2 = math.radians(a["lat"]), math.radians(b["lat"])
    dp, dl = p2 - p1, math.radians(b["lon"] - a["lon"])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _row(p: Mapping[str, Any], delta: float, confidence: str, via: dict | None, source: str | None = None) -> dict:
    return {"lat": p["lat"], "lon": p["lon"], "alt": p["alt"], "source": source or p["source"],
            "source_ref": p["source_ref"], "delta_s": round(delta), "confidence": confidence,
            "place_name": p["place_name"], "city": p["city"], "state": p["state"], "country": p["country"],
            "score": SCORE[confidence], "via": via}


def _nearest_each_side(conn, target: datetime, span_s: int, own: str) -> tuple[tuple | None, tuple | None]:
    """(point, gap_s) nearest before and nearest after the target within ``span_s``, excluding the photo's own point."""
    before = after = None
    for p in repo.geo_points_between(conn, utc_text(target - timedelta(seconds=span_s)),
                                     utc_text(target + timedelta(seconds=span_s))):
        if p["source_ref"] == own:
            continue
        gap = (datetime.fromisoformat(p["utc_time"]) - target).total_seconds()
        if gap < 0 and (before is None or gap > before[1]):
            before = (p, gap)
        elif gap > 0 and (after is None or gap < after[1]):
            after = (p, gap)
    return before, after


def _farther_rows(conn, target: datetime, window_s: int, reach_s: int, own: str) -> list[dict]:
    """D-075: rows for a photo with nothing inside the window; ``far`` / ``weak`` tiers out to ``reach_s``."""
    before, after = _nearest_each_side(conn, target, reach_s, own)
    sides = [x for x in (before, after) if x]
    both = len(sides) == 2
    rows = []
    for p, g in sides:
        tier = _tier(abs(g), window_s, reach_s) or "weak"
        rows.append(_row(p, g, tier if both else _shift(tier, 1), None))
    if both:
        lo, hi = before[0], after[0]
        km = _km(lo, hi)
        wider = max(-before[1], after[1])
        tier = _tier(wider, window_s, reach_s) or "weak"
        stayed = km < NEAR_KM or (any(_place_key(lo)) and _place_key(lo) == _place_key(hi))
        tier = _shift(tier, -1 if stayed else 1)
        frac = -before[1] / (after[1] - before[1])
        row = _row(lo, 0, tier, None, "interpolated")
        row.update(lat=lo["lat"] + frac * (hi["lat"] - lo["lat"]), lon=lo["lon"] + frac * (hi["lon"] - lo["lon"]),
                   alt=None, source_ref=f"{lo['source_ref']}|{hi['source_ref']}",
                   ends={"before_s": round(before[1]), "after_s": round(after[1]), "km": round(km, 1),
                         "stayed": stayed, "before_place": lo["place_name"] or lo["city"],
                         "after_place": hi["place_name"] or hi["city"]})
        rows.append(row)
    return rows


def suggest(conn: sqlite3.Connection, group_id: int, *, window_s: int = 1800, limit: int = 3,
            reach_s: int = 43200, extended: bool = True) -> list[dict]:
    """Geotag candidates for one group, nearest in time first.

    Points within ``±window_s`` of the group's UTC, except the group's own camera point
    (``source_ref == "group:<id>"``). When one point lies before the frame and one after (both inside the
    window), the first suggestion is their linear interpolation (source ``interpolated``, delta 0, confidence from
    the wider of the two gaps). Each dict: lat, lon, alt, source, source_ref, delta_s, confidence, and the point's
    place_name, city, state, country (None for an interpolated one).

    ``extended`` (sp-8, D-074) then appends, after the ``limit`` cut, rows that carry a ``via`` key: (1) ``session``
    rows, phone points within the window of another frame of the photo's session (``via`` = that frame's stem and
    delta; confidence is that frame's tier); (2) when nothing is within the window, ``far`` / ``weak`` rows (D-075):
    the nearest point each side within ``reach_s`` (12 h), one-sided evidence lowered a tier, plus an
    ``interpolated`` row with an ``ends`` key, raised a tier when the ends are under 1 km apart or share a place,
    else lowered; (3) when nothing is within reach, ``hint`` rows, the nearest each side within 48 h. Every row
    has a ``score`` (0..1); the extra rows are sorted by it.
    """
    row = conn.execute(
        """
        SELECT g.body_id, g.capture_time, g.capture_offset, COALESCE(m.time_shift_s, 0) AS time_shift_s
        FROM capture_groups g LEFT JOIN group_meta m ON m.group_id = g.group_id
        WHERE g.group_id = ?
        """,
        (group_id,),
    ).fetchone()
    if row is None:
        raise ValueError(f"no capture group {group_id}")
    target = group_utc(row, repo.get_setting(conn, "default_offset", "+00:00"), BodyClocks.load(conn))
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
                       "score": SCORE[_confidence(abs(gap))],
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
                             "delta_s": 0, "confidence": _confidence(wider), "score": SCORE[_confidence(wider)],
                             "place_name": None, "city": None, "state": None, "country": None})

    points.sort(key=lambda p: abs(p["_gap"]))
    for p in points:
        del p["_gap"]
    out = (interpolated + points)[:limit]
    if not extended:
        return out
    seen = {(p["source"], p["source_ref"]) for p in points}
    head = list(out)
    out += _session_rows(conn, group_id, target, window_s, seen, limit)
    if not points and reach_s > 0:
        far = _farther_rows(conn, target, window_s, reach_s, own)
        out += far
        if not far:
            lo, hi = _nearest_each_side(conn, target, HINT_S, own)
            out += [_row(x[0], x[1], "hint", None) for x in (lo, hi) if x]
        out[len(head):] = sorted(out[len(head):], key=lambda r: -r["score"])
    return out


def _session_rows(conn, group_id: int, target: datetime, window_s: int, seen: set, limit: int) -> list[dict]:
    frames = [f for f in session_of(conn, group_id) if f["group_id"] != group_id]
    if not frames:
        return []
    times = [f["utc"] for f in frames]
    w = timedelta(seconds=window_s)
    own = f"group:{group_id}"
    best: dict[tuple, tuple[float, dict, dict]] = {}
    for p in repo.geo_points_between(conn, utc_text(times[0] - w), utc_text(times[-1] + w)):
        if p["source_ref"] == own or (p["source"], p["source_ref"]) in seen:
            continue
        t = datetime.fromisoformat(p["utc_time"])
        i = bisect.bisect_left(times, t)
        cands = [frames[j] for j in (i - 1, i) if 0 <= j < len(frames)]
        f = min(cands, key=lambda c: abs((c["utc"] - t).total_seconds()))
        d = (t - f["utc"]).total_seconds()
        if abs(d) > window_s:
            continue
        key = _place_key(p) if any(_place_key(p)) else ("ref", p["source"], p["source_ref"])
        if key not in best or abs(d) < best[key][0]:
            best[key] = (abs(d), p, f)
    rows = []
    for ad, p, f in sorted(best.values(), key=lambda b: b[0])[:limit]:
        gap = (datetime.fromisoformat(p["utc_time"]) - target).total_seconds()
        d = (datetime.fromisoformat(p["utc_time"]) - f["utc"]).total_seconds()
        rows.append(_row(p, gap, _confidence(ad), {"stem": f["stem"], "delta_s": round(d)}, "session"))
    return rows


def places_near(conn: sqlite3.Connection, group_id: int, *, window_s: int = 3600) -> list[dict]:
    """Distinct named places of phone points within ``±window_s`` of a group (sp-3), nearest in time first.

    Only points that carry a place name, city, state or country count. Each dict: place_name, city, state,
    country, a representative lat/lon/alt (the point nearest in time) and that point's delta_s.
    """
    found = suggest(conn, group_id, window_s=window_s, limit=10**6, extended=False)
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
