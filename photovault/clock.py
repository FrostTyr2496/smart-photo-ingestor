"""Camera clock suggestions (sp-7, D-070): find a body whose clock or zone is off, from the phone timeline.

The phone's GPS fixes (sources iphone/ipad/glasses) are the reference. The timeline is split into runs where the
fix's `local_offset` stays the same (a trip). For each camera body and run, shifts from -14 h to +14 h in 15-min
steps are tried on the frames' current corrected UTC, counting frames within +-10 min of some phone fix. A shift is
suggested only when it is not 0, has at least `min_matches` matches and at least twice the runner-up's. Judgement
call: the runner-up ignores shifts within 1 h of the best, so the neighbouring steps do not count against it.
Shifts of a few minutes cannot be told apart this way. Metadata only (rule 9).
"""

from __future__ import annotations

import sqlite3
from bisect import bisect_left
from datetime import datetime

from photovault.catalog import repo
from photovault.geo import group_utc

PHONE_SOURCES = ("iphone", "ipad", "glasses")
STEP_S = 900
MAX_SHIFT_S = 14 * 3600
TOLERANCE_S = 600
RUNNER_GAP_S = 3600
SHIFTS = list(range(-MAX_SHIFT_S, MAX_SHIFT_S + 1, STEP_S))


def _phone_points(conn: sqlite3.Connection) -> list[tuple[float, str | None]]:
    marks = ",".join("?" * len(PHONE_SOURCES))
    rows = conn.execute(f"SELECT utc_time, local_offset FROM geo_points WHERE source IN ({marks}) ORDER BY utc_time",
                        PHONE_SOURCES).fetchall()
    return [(datetime.fromisoformat(r["utc_time"]).timestamp(), r["local_offset"]) for r in rows]


def _runs(points: list[tuple[float, str | None]]) -> list[tuple[float, float]]:
    runs: list[tuple[float, float]] = []
    start = prev = None
    offset: object = object()
    for t, off in points:
        if off != offset:
            if start is not None:
                runs.append((start, prev))
            start, offset = t, off
        prev = t
    if start is not None:
        runs.append((start, prev))
    return runs


def _matches(frames: list[float], times: list[float], shift: int) -> int:
    n = 0
    last = len(times)
    for f in frames:
        t = f + shift
        i = bisect_left(times, t)
        if (i < last and times[i] - t <= TOLERANCE_S) or (i > 0 and t - times[i - 1] <= TOLERANCE_S):
            n += 1
    return n


def suggest_offsets(conn: sqlite3.Connection, *, min_matches: int = 20) -> list[dict]:
    """Suggestions, one per (body, phone run). Each: body_id, body, first_date, last_date, frames, current_shifts,
    delta_s, matches, runner_up, and `groups`: [{time_shift_s (the suggested one), group_ids}] to apply."""
    points = _phone_points(conn)
    if not points:
        return []
    times = [t for t, _ in points]
    runs = _runs(points)
    default_offset = repo.get_setting(conn, "default_offset", "+00:00")
    rows = conn.execute(
        """SELECT g.group_id, g.body_id, b.nickname, g.capture_time, g.capture_offset,
                  COALESCE(m.time_shift_s, 0) AS time_shift_s
           FROM capture_groups g JOIN bodies b ON b.body_id = g.body_id
           LEFT JOIN group_meta m ON m.group_id = g.group_id
           WHERE g.body_id IS NOT NULL AND g.capture_time IS NOT NULL""").fetchall()
    by_body: dict[int, list[dict]] = {}
    for r in rows:
        when = group_utc(r, default_offset)
        if when is None:
            continue
        by_body.setdefault(r["body_id"], []).append(
            {"id": r["group_id"], "t": when.timestamp(), "shift": int(r["time_shift_s"]),
             "date": str(r["capture_time"])[:10], "nick": r["nickname"]})

    out: list[dict] = []
    for body_id, frames in by_body.items():
        frames.sort(key=lambda f: f["t"])
        ftimes = [f["t"] for f in frames]
        for start, end in runs:
            lo = bisect_left(ftimes, start - MAX_SHIFT_S)
            hi = bisect_left(ftimes, end + MAX_SHIFT_S + 1)
            cand = frames[lo:hi]
            if len(cand) < min_matches:
                continue
            ct = [f["t"] for f in cand]
            counts = {s: _matches(ct, times, s) for s in SHIFTS}
            best = max(SHIFTS, key=lambda s: (counts[s], -abs(s)))
            if best == 0 or counts[best] < min_matches:
                continue
            runner = max((c for s, c in counts.items() if abs(s - best) > RUNNER_GAP_S), default=0)
            if counts[best] < 2 * runner:
                continue
            chosen = [f for f in cand if start - TOLERANCE_S <= f["t"] + best <= end + TOLERANCE_S]
            if not chosen:
                continue
            by_shift: dict[int, list[int]] = {}
            for f in chosen:
                by_shift.setdefault(f["shift"] + best, []).append(f["id"])
            dates = sorted(f["date"] for f in chosen)
            out.append({
                "body_id": body_id, "body": chosen[0]["nick"], "first_date": dates[0], "last_date": dates[-1],
                "frames": len(chosen), "current_shifts": sorted({f["shift"] for f in chosen}), "delta_s": best,
                "matches": counts[best], "runner_up": runner,
                "groups": [{"time_shift_s": s, "group_ids": ids} for s, ids in sorted(by_shift.items())],
            })
    out.sort(key=lambda s: (s["first_date"], s["body_id"]))
    return out
