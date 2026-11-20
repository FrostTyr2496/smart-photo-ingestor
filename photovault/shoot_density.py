"""Phone density and split suggestions (sh-11, D-141).

Where iPhone photos are dense the user was active, and a large camera shoot there may really be several outings.
``annotate`` adds ``phone_density`` and ``split_suggestions`` to each unlocked shoot (and the ``may_split`` flag) and
``place_density`` adds ``phone_density`` to place spots. It only suggests: a split stays the user's ``split`` decision.
"""

from __future__ import annotations

import statistics
from bisect import bisect_left, bisect_right
from typing import Callable

from .shoot_scoring import (MAX_SPLIT_SUGGESTIONS, SPLIT_MOVE_KM, SPLIT_WINDOW_S, Config, _jdict, _jlist)

DENSITY_PAD_S = 3600
DENSE_RATIO = 1.0         # above this the thresholds ease
EASE_PER_RATIO, EASE_MAX = 0.10, 0.5


def _density(points: int, hours: float) -> float:
    return points / hours if hours > 0 else 0.0


def _terms(phone, i0: float, i1: float) -> tuple[list, list]:
    """Phone points before and after a gap between frames at ``i0`` and ``i1`` (seconds)."""
    mid = (i0 + i1) / 2
    lo = bisect_left(phone.ts, i0 - SPLIT_WINDOW_S)
    mid_i = bisect_left(phone.ts, mid)
    hi = bisect_right(phone.ts, i1 + SPLIT_WINDOW_S)
    return phone.pts[lo:mid_i], phone.pts[mid_i:hi]


def _centre(pts: list[tuple[float, float]]) -> tuple[float, float] | None:
    return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)) if pts else None


def _topics(pts: list, stop: set[str]) -> set[str]:
    out: set[str] = set()
    for p in pts:
        ana = p[8] if len(p) > 8 else None
        if not ana:
            continue
        out |= {v.lower() for v in _jlist(ana[1]) if v.lower() not in stop}
        out |= {str(v).lower() for v in (_jdict(ana[3]).get("activities") or []) if isinstance(v, str)}
    return out


def _gap_candidate(items: list, k: int, phone, cfg: Config, dist: Callable, ratio: float, max_gap: float) -> dict | None:
    a, b = items[k], items[k + 1]
    gap = b.ts - a.ts
    before, after = _terms(phone, a.ts, b.ts)
    reasons: list[tuple[float, str]] = []
    w = cfg.split_weights

    # place: how far the evidence moved, or a different venue
    place = None
    bp = [(p[1], p[2]) for p in before if p[1] is not None]
    ap = [(p[1], p[2]) for p in after if p[1] is not None]
    if not (bp and ap):
        bp = [(i.lat, i.lon) for i in items[max(0, k - 20):k + 1] if i.lat is not None and a.ts - i.ts <= SPLIT_WINDOW_S]
        ap = [(i.lat, i.lon) for i in items[k + 1:k + 22] if i.lat is not None and i.ts - b.ts <= SPLIT_WINDOW_S]
    cb, ca = _centre(bp), _centre(ap)
    if cb and ca:
        d = dist(*cb, *ca)
        lo, hi = SPLIT_MOVE_KM
        place = max(0.0, min(1.0, (d - lo) / (hi - lo)))
        if place > 0:
            reasons.append((place, f"{'phone' if before and after else 'camera'} moved {d:.1f} km"))
    vb = {p[4] for p in before if p[4]}
    va = {p[4] for p in after if p[4]}
    if vb and va:
        venue = 0.6 if not (vb & va) else 0.0
        place = max(place or 0.0, venue)
        if venue:
            reasons.append((venue, "different venue"))

    # subject: the phone labels and activities change
    subject = None
    tb, ta = _topics(before, cfg.stoplist), _topics(after, cfg.stoplist)
    if tb and ta:
        subject = 1.0 - len(tb & ta) / len(tb | ta)
        if subject >= 0.5:
            reasons.append((subject * 0.9, f"phone labels change: {sorted(tb - ta)[0] if tb - ta else '…'} → {sorted(ta - tb)[0] if ta - tb else '…'}"))

    if place is None and subject is None:
        return None  # nothing to compare either side: no suggestion
    gap_t = min(1.0, gap / 7200) * (0.5 + 0.5 * gap / max_gap)
    terms = {"place": place, "subject": subject, "gap": gap_t}
    known = {k_: v for k_, v in terms.items() if v is not None}
    score = sum(w[k_] * v for k_, v in known.items()) / sum(w[k_] for k_ in known)
    if ratio > DENSE_RATIO:
        score *= 1 + min(EASE_MAX, EASE_PER_RATIO * (ratio - DENSE_RATIO))
        reasons.append((0.1, f"busy area: {ratio:.1f}x usual phone density"))
    if gap >= 3600:
        reasons.append((gap_t * 0.5, f"{gap / 3600:.1f} h gap"))
    else:
        reasons.append((gap_t * 0.5, f"{round(gap / 60)} min gap"))
    top = [t for _, t in sorted(reasons, key=lambda r: -r[0])[:3]]
    return {"after_group_id": a.gid, "gap_s": int(gap), "score": round(min(1.0, score), 3), "reasons": top}


def annotate(shoots: list[dict], phone, cfg: Config, dist: Callable) -> None:
    """Add ``phone_density`` to every unlocked shoot; ``split_suggestions`` and ``may_split`` where a gap scores."""
    todo = [s for s in shoots if not s.get("locked") and s.get("_items")]
    for s in todo:
        t0, t1 = s["_t0"], s["_t1"]
        pts = bisect_right(phone.ts, t1 + DENSITY_PAD_S) - bisect_left(phone.ts, t0 - DENSITY_PAD_S)
        hours = (t1 - t0 + 2 * DENSITY_PAD_S) / 3600
        s["phone_density"] = {"points": pts, "per_hour": round(_density(pts, hours), 3), "ratio_to_median": 0.0}
        s["_density_hours"] = hours
    rates = [s["phone_density"]["per_hour"] for s in todo if s["phone_density"]["per_hour"] > 0]
    median = statistics.median(rates) if rates else 0.0
    for s in todo:
        d = s["phone_density"]
        d["ratio_to_median"] = round(d["per_hour"] / median, 2) if median else 0.0
        items = s["_items"]
        gaps = [(items[k + 1].ts - items[k].ts, k) for k in range(len(items) - 1)]
        cand_idx = [k for g, k in gaps if g >= cfg.split_min_gap_s]
        if not cand_idx or not phone.ts and not any(i.lat is not None for i in items):
            continue
        max_gap = max(g for g, _ in gaps) or 1.0
        out = []
        for k in cand_idx:
            c = _gap_candidate(items, k, phone, cfg, dist, d["ratio_to_median"], max_gap)
            if c is None:
                continue
            if c["score"] < cfg.show:
                if not cfg.beta:
                    continue
                c["beyond_cutoff"] = True
            out.append(c)
        out.sort(key=lambda c: (-c["score"], c["after_group_id"]))
        out = out[:MAX_SPLIT_SUGGESTIONS]
        if out:
            s["split_suggestions"] = out
            if out[0]["score"] >= cfg.pretick:
                s["flags"].append("may_split")


def place_density(spots: list[dict], by_id: dict[str, dict]) -> None:
    """``phone_density`` on each spot: phone points per visit-hour against the median spot."""
    rows = []
    for sp in spots:
        mem = [by_id[m["proposal_id"]] for m in sp["members"] if m["proposal_id"] in by_id and by_id[m["proposal_id"]].get("phone_density")]
        pts = sum(m["phone_density"]["points"] for m in mem)
        hours = sum(m.get("_density_hours", 0.0) for m in mem)
        rows.append((sp, pts, _density(pts, hours)))
    median = statistics.median([r for *_, r in rows if r > 0] or [0.0])
    for sp, pts, rate in rows:
        sp["phone_density"] = {"points": pts, "per_hour": round(rate, 3), "ratio_to_median": round(rate / median, 2) if median else 0.0}
