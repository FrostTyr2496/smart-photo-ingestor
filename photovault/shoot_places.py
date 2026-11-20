"""Place collections (pl-1, D-138): shoots grouped by where, across time.

``build(shoots, phone, cfg, dist, off_seconds)`` returns ``{"spots": [...], "areas": [...]}`` for the plan file's
``places``. A spot is a single-link cluster of shoot centroids (``place_spot_km``), cut where its diameter would pass
``place_spot_max_km``. An area groups spots within ``place_area_km`` that share a phone area name. Shoots with no
coordinates join only through a user-confirmed place name or a shared human folder. Pure: reads nothing, writes nothing.
"""

from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Callable

from photovault.catalog import repo

NIGHT_START, NIGHT_END = 22, 6
MIN_NIGHT_POINTS = 10
MIN_RADIUS_KM = 0.25
# name priority: user > phone venue > phone place > folder > area
RANK = {"user": 0, "phone_venue": 1, "phone_place": 2, "meta": 2, "folder": 3, "phone_area": 4}
NEUTRAL_PLACE_CONF = 0.5   # a shoot with coordinates but no place name
WORD_JOIN_NEARNESS = 0.8   # joined by words: the name, not the distance, links it


@dataclass
class PlaceConfig:
    spot_km: float = 1.5
    spot_max_km: float = 5.0
    area_km: float = 25.0
    min_shoots: int = 2
    show: float = 0.1
    beta: bool = True


def _float(conn, key: str, default: float) -> float:
    try:
        return float(repo.get_setting(conn, key, None))
    except (TypeError, ValueError):
        return default


def load_config(conn, scoring) -> PlaceConfig:
    """Settings ``place_spot_km`` (1.5), ``place_spot_max_km`` (5), ``place_area_km`` (25), ``place_min_shoots`` (2);
    the cut-off and beta come from the shoot scoring config."""
    return PlaceConfig(spot_km=_float(conn, "place_spot_km", 1.5), spot_max_km=_float(conn, "place_spot_max_km", 5.0),
                       area_km=_float(conn, "place_area_km", 25.0),
                       min_shoots=max(1, int(_float(conn, "place_min_shoots", 2))),
                       show=scoring.show, beta=scoring.beta)


def _key(prefix: str, ids: list[str]) -> str:
    return prefix + hashlib.sha1("|".join(sorted(ids)).encode()).hexdigest()[:12]


def _cluster(pts: list[tuple[float, float]], link_km: float, max_km: float | None, dist: Callable) -> list[list[int]]:
    """Single-link clusters of point indexes; a merge is refused when it would pass ``max_km`` across."""
    n = len(pts)
    edges = sorted((dist(*pts[i], *pts[j]), i, j) for i in range(n) for j in range(i + 1, n))
    groups: dict[int, list[int]] = {i: [i] for i in range(n)}
    owner = list(range(n))
    for d, i, j in edges:
        if d > link_km:
            break
        a, b = owner[i], owner[j]
        if a == b:
            continue
        if max_km is not None and any(dist(*pts[x], *pts[y]) > max_km for x in groups[a] for y in groups[b]):
            continue
        for x in groups[b]:
            owner[x] = a
        groups[a] += groups.pop(b)
    return sorted((sorted(g) for g in groups.values()), key=lambda g: g[0])


def _candidates(s: dict) -> list[tuple[str, str]]:
    """Place-name candidates ``(value, source)`` of one shoot: its place detail and alternatives, else its own name."""
    place = (s.get("details") or {}).get("place")
    out: list[tuple[str, str]] = []
    if place:
        if place.get("value"):
            out.append((place["value"], place.get("source") or "phone_place"))
        out += [(a["value"], a.get("source") or "phone_place") for a in place.get("alternatives", [])
                if a.get("value") and not a.get("beyond_cutoff")]
    elif s.get("name", {}).get("value"):
        out.append((s["name"]["value"], "folder"))
    return out


def _confirmed(s: dict) -> str | None:
    place = (s.get("details") or {}).get("place") or {}
    return place["value"].strip().lower() if place.get("source") == "user" and place.get("value") else None


def _name(members: list[dict]) -> dict:
    ids: dict[str, set[str]] = {}
    shown: dict[str, str] = {}
    rank: dict[str, int] = {}
    src: dict[str, str] = {}
    for s in members:
        for v, source in _candidates(s):
            low = v.lower()
            ids.setdefault(low, set()).add(s["proposal_id"])
            shown.setdefault(low, v)
            r = RANK.get(source, 3)
            if low not in rank or r < rank[low]:
                rank[low], src[low] = r, source
    ordered = sorted(ids, key=lambda k: (rank[k], -len(ids[k]), shown[k]))
    if not ordered:
        return {"value": None, "source": None, "confidence": 0.0, "count": 0, "alternatives": []}
    top = ordered[0]
    return {"value": shown[top], "source": src[top], "confidence": round(len(ids[top]) / len(members), 3),
            "count": len(ids[top]),
            "alternatives": [{"value": shown[k], "source": src[k], "count": len(ids[k])} for k in ordered[1:9]]}


def _area_names(members: list[dict]) -> Counter:
    c: Counter = Counter()
    for s in members:
        place = (s.get("details") or {}).get("place") or {}
        c.update({a["value"] for a in [place, *place.get("alternatives", [])]
                  if a.get("source") == "phone_area" and a.get("value") and not a.get("beyond_cutoff")})
    return c


def _night_counts(phone, spots: list[dict], cfg: PlaceConfig, dist: Callable, off_seconds: Callable) -> Counter:
    """Phone points between 22:00 and 06:00 local, counted by the nearest spot within its reach."""
    out: Counter = Counter()
    cache: dict = {}
    for p in phone.pts:
        if p[1] is None or p[2] is None:
            continue
        hour = datetime.fromtimestamp(p[0] + off_seconds(p[3], cache), UTC).hour
        if NIGHT_END <= hour < NIGHT_START:
            continue
        best, bd = None, None
        for sp in spots:
            c = sp["centroid"]
            if abs(c["lat"] - p[1]) > 0.2:  # cheap prefilter, about 22 km
                continue
            d = dist(c["lat"], c["lon"], p[1], p[2])
            if d <= max(sp["radius_km"], cfg.spot_km) + cfg.spot_km and (bd is None or d < bd):
                best, bd = sp["key"], d
        if best:
            out[best] += 1
    return out


def build(shoots: list[dict], phone, cfg: PlaceConfig, dist: Callable, off_seconds: Callable) -> dict:
    located = [s for s in shoots if s.get("centroid")]
    pts = [(s["centroid"]["lat"], s["centroid"]["lon"]) for s in located]
    spots: list[dict] = []
    for idx in _cluster(pts, cfg.spot_km, cfg.spot_max_km, dist):
        lat = sum(pts[i][0] for i in idx) / len(idx)
        lon = sum(pts[i][1] for i in idx) / len(idx)
        radius = max(MIN_RADIUS_KM, max(dist(lat, lon, *pts[i]) for i in idx))
        spots.append({"_group": [located[i] for i in idx],
                      "_dist": {located[i]["proposal_id"]: dist(lat, lon, *pts[i]) for i in idx}, "_via": {},
                      "centroid": {"lat": round(lat, 5), "lon": round(lon, 5)}, "radius_km": round(radius, 3)})

    # shoots without coordinates: a confirmed name or a shared human folder, never the phone area alone
    for s in shoots:
        if s.get("centroid"):
            continue
        name, humans = _confirmed(s), s.get("_humans") or set()
        hits = []
        for sp in spots:
            if name and any(_confirmed(m) == name for m in sp["_group"]):
                hits.append((sp, "name"))
            elif humans and any(humans & (m.get("_humans") or set()) for m in sp["_group"]):
                hits.append((sp, "folder"))
        if len(hits) == 1:  # a name or folder shared by several spots is ambiguous: join nothing
            sp, via = hits[0]
            sp["_group"].append(s)
            sp["_via"][s["proposal_id"]] = via

    for sp in spots:
        sp["_group"].sort(key=lambda s: (s["start_instant"], s["proposal_id"]))
        sp["key"] = _key("p:", [s["proposal_id"] for s in sp["_group"]])

    nights = _night_counts(phone, spots, cfg, dist, off_seconds) if phone is not None and spots else Counter()
    home_key, home_n = nights.most_common(1)[0] if nights else (None, 0)
    if home_n < MIN_NIGHT_POINTS:
        home_key = None

    spots_out: list[dict] = []
    for sp in spots:
        group = sp["_group"]
        if len(group) < cfg.min_shoots and sp["key"] != home_key:
            continue
        members = []
        shown = []
        for s in group:
            via = sp["_via"].get(s["proposal_id"], "coords")
            place = (s.get("details") or {}).get("place") or {}
            if s.get("locked"):
                pconf = 1.0
            else:
                pconf = place["confidence"] if place.get("value") else NEUTRAL_PLACE_CONF
            near = (1.0 - 0.5 * min(1.0, sp["_dist"][s["proposal_id"]] / sp["radius_km"])
                    if via == "coords" else WORD_JOIN_NEARNESS)
            m = {"proposal_id": s["proposal_id"], "confidence": round(near * pconf, 3), "via": via}
            if m["confidence"] < cfg.show:
                if not cfg.beta:
                    continue
                m["beyond_cutoff"] = True
            members.append(m)
            shown.append(s)
        if not members:
            continue
        spots_out.append({
            "key": sp["key"], "kind": "spot", "name": _name(shown), "centroid": sp["centroid"],
            "radius_km": sp["radius_km"], "area_key": None, "members": members,
            "frames": sum(s["counts"]["frames"] for s in shown),
            "first_date": min(s["shoot_date"] for s in shown), "last_date": max(s["shoot_date"] for s in shown),
            "home": sp["key"] == home_key, "_names": _area_names(shown)})

    # areas: spots within area_km that share a phone area name (or where one side has none)
    apts = [(s["centroid"]["lat"], s["centroid"]["lon"]) for s in spots_out]
    parent = list(range(len(apts)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(apts)):
        for j in range(i + 1, len(apts)):
            a, b = spots_out[i]["_names"], spots_out[j]["_names"]
            if dist(*apts[i], *apts[j]) <= cfg.area_km and (not a or not b or set(a) & set(b)):
                parent[find(j)] = find(i)
    buckets: dict[int, list[dict]] = {}
    for i, sp in enumerate(spots_out):
        buckets.setdefault(find(i), []).append(sp)
    areas: list[dict] = []
    for grp in buckets.values():
        names: Counter = Counter()
        for sp in grp:
            names.update(sp["_names"])
        best: dict[str, float] = {}
        for sp in grp:
            for m in sp["members"]:
                best[m["proposal_id"]] = max(best.get(m["proposal_id"], 0.0), m["confidence"])
        key = _key("a:", list(best))
        if names:
            value, count = sorted(names.items(), key=lambda kv: (-kv[1], kv[0]))[0]
            name = {"value": value, "source": "phone_area", "confidence": round(count / len(best), 3), "count": count,
                    "alternatives": [{"value": v, "source": "phone_area", "count": c}
                                     for v, c in sorted(names.items(), key=lambda kv: (-kv[1], kv[0])) if v != value][:8]}
        else:
            name = dict(max(grp, key=lambda sp: (len(sp["members"]), sp["frames"]))["name"])
        for sp in grp:
            sp["area_key"] = key
        areas.append({"key": key, "kind": "area", "name": name, "spot_keys": [sp["key"] for sp in grp],
                      "members": [{"proposal_id": k, "confidence": best[k]} for k in sorted(best)]})
    for sp in spots_out:
        del sp["_names"]
    spots_out.sort(key=lambda sp: (-len(sp["members"]), -sp["frames"], sp["key"]))
    areas.sort(key=lambda a: (-len(a["members"]), a["key"]))
    return {"spots": spots_out, "areas": areas}
