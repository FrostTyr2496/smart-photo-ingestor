"""The trips outline (rv-46, D-134): every capture group placed in a trip, a shoot or a loose day.

Pure and read-only. A frame belongs to an **accepted** shoot when ``capture_groups.shoot_id`` is set (its trip comes
from ``shoots.trip_id``); otherwise to a **proposed** shoot when the newest plan's ``shoots[].group_ids`` holds it;
otherwise it is **loose** and listed by day. Decisions saved after the plan was made are not folded in (Re-plan does
that). ``outline`` answers the Photos and Files views; ``resolve_key`` turns a shoot key into its group ids.
"""

from __future__ import annotations

import json
from bisect import bisect_left, bisect_right
import re
import sqlite3
from datetime import datetime, timedelta
from collections import Counter, defaultdict
from typing import Any

from photovault.catalog import repo

UNDATED = repo.UNDATED
LOOSE_PREFIX = "loose:"
OTHER_KEY = "other"  # files with no photo group (non-photo or not hashed), on the drive-files endpoints

_INDEX_CACHE: dict[str, tuple[dict, dict]] = {}


def proposal_index(plan: dict | None) -> dict[int, str]:
    """``{group_id: proposal_id}`` for the plan's unlocked shoots, built once per plan object (cached by plan id)."""
    if not plan:
        return {}
    pid = str(plan.get("plan_id") or "")
    hit = _INDEX_CACHE.get(pid)
    if hit is not None and hit[0] is plan:
        return hit[1]
    index: dict[int, str] = {}
    for s in plan.get("shoots") or []:
        if s.get("locked"):
            continue
        for gid in s.get("group_ids") or []:
            index[int(gid)] = s["proposal_id"]
    _INDEX_CACHE.clear()
    _INDEX_CACHE[pid] = (plan, index)
    return index


def _text(value: Any) -> str | None:
    """A plan name or detail: ``{"value": ...}`` or a plain string."""
    if isinstance(value, dict):
        value = value.get("value")
    return (value.strip() or None) if isinstance(value, str) else None


def title_of(place: str | None, subject: str | None, name: str) -> str:
    """``place · subject``, falling back to the shoot's name."""
    parts = [p for p in (place, subject) if p]
    return " · ".join(parts) if parts else name


def _spread(ids: list[int], n: int = 8) -> list[int]:
    if len(ids) <= n:
        return list(ids)
    return [ids[round(i * (len(ids) - 1) / (n - 1))] for i in range(n)]


def _scope(conn: sqlite3.Connection, volume_id: str | None, missing: str | None) -> list[sqlite3.Row]:
    where, params = repo._group_filter_sql(volume_id=volume_id, shoot_id=None, missing=missing)
    return conn.execute(
        f"""SELECT g.group_id, g.shoot_id, g.capture_time, b.nickname AS body, m.place_name AS saved_place,
                  m.city AS saved_city
            FROM capture_groups g
            LEFT JOIN group_meta m ON m.group_id = g.group_id
            LEFT JOIN bodies b ON b.body_id = g.body_id
            WHERE {where}""", params).fetchall()


def _file_counts(conn: sqlite3.Connection, volume_id: str) -> dict[int | None, tuple[int, int]]:
    """``{group_id | None: (files, bytes)}`` of the instances on one drive (None = no group: non-photo or unhashed)."""
    out: dict[int | None, tuple[int, int]] = {}
    for r in conn.execute(
        """SELECT a.group_id AS gid, COUNT(*) AS n, COALESCE(SUM(i.size), 0) AS b FROM instances i
           LEFT JOIN assets a ON a.asset_id = i.asset_id
           WHERE i.volume_id = ? AND i.state IN ('indexed', 'verified') GROUP BY a.group_id""", (volume_id,)):
        out[r["gid"]] = (int(r["n"]), int(r["b"]))
    return out


def _plan_shoot(plan: dict | None, key: str) -> dict:
    for s in (plan or {}).get("shoots") or []:
        if s.get("proposal_id") == key:
            return s
    return {}


def _confidence(details: dict) -> float | None:
    """A proposed shoot's confidence for shading its name: the lower of its place and subject confidence among those
    proposed (sh-8 details), or None when the plan has neither (a plan made before sh-8)."""
    vals = [float(d["confidence"]) for d in (details.get("place"), details.get("subject"))
            if isinstance(d, dict) and d.get("value") and isinstance(d.get("confidence"), (int, float))]
    return round(min(vals), 3) if vals else None


def _short(name: str | None) -> str | None:
    if not name:
        return None
    first = name.split(",")[0].strip()
    return first or name.strip()


def _wall(utc_iso: str, offset: str | None) -> str | None:
    """The wall-clock time (``YYYY-MM-DDTHH:MM:SS``) of a UTC instant in its own offset."""
    try:
        t = datetime.fromisoformat(utc_iso)
        if offset and re.fullmatch(r"[+-]\d{2}:\d{2}", offset):
            sign = 1 if offset[0] == "+" else -1
            t = t + sign * timedelta(hours=int(offset[1:3]), minutes=int(offset[4:6]))
        return t.strftime("%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        return None


def _named_points(conn: sqlite3.Connection) -> tuple[list[str], list[str]]:
    """Every place name the catalog knows at a moment, sorted by wall time: phone fixes with a place (in their own
    offset), photos with a saved place, and located photos without a name, named by the nearest named point on the map
    (within ~25 km). Used to label a shoot with no place of its own by the nearest place in time (D-148)."""
    named: list[tuple[str, str]] = []
    grid: dict[tuple[int, int], list[tuple[float, float, str]]] = defaultdict(list)
    cell = lambda lat, lon: (int(lat * 4 // 1), int(lon * 4 // 1))  # noqa: E731  (0.25 degree cells)
    for r in conn.execute("SELECT utc_time, local_offset, lat, lon, COALESCE(city, place_name) AS name FROM geo_points "
                          "WHERE COALESCE(city, place_name) IS NOT NULL"):
        w = _wall(r["utc_time"], r["local_offset"])
        name = _short(r["name"])
        if w and name:
            named.append((w, name))
            if r["lat"] is not None and r["lon"] is not None:
                grid[cell(r["lat"], r["lon"])].append((r["lat"], r["lon"], name))
    unnamed: list[tuple[str, float, float]] = []
    for r in conn.execute("SELECT g.capture_time, m.lat, m.lon, COALESCE(m.city, m.place_name) AS name "
                          "FROM group_meta m JOIN capture_groups g ON g.group_id = m.group_id "
                          "WHERE g.capture_time IS NOT NULL AND (m.lat IS NOT NULL OR COALESCE(m.city, m.place_name) IS NOT NULL)"):
        w = str(r["capture_time"])[:19]
        if r["name"]:
            named.append((w, _short(r["name"]) or r["name"]))
            if r["lat"] is not None and r["lon"] is not None:
                grid[cell(r["lat"], r["lon"])].append((r["lat"], r["lon"], _short(r["name"]) or r["name"]))
        elif r["lat"] is not None and r["lon"] is not None:
            unnamed.append((w, r["lat"], r["lon"]))
    for w, lat, lon in unnamed:
        cy, cx = cell(lat, lon)
        best, dist = None, 0.25 ** 2
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                for plat, plon, name in grid.get((cy + dy, cx + dx), ()):
                    d = (plat - lat) ** 2 + (plon - lon) ** 2
                    if d < dist:
                        best, dist = name, d
        if best:
            named.append((w, best))
    named.sort()
    return [w for w, _ in named], [n for _, n in named]


NEAR_HALF_LIFE_S = 3 * 3600  # a "Near" label fades: 0.35 at the shoot, half that 3 h away, and so on


def _near(times: list[str], names: list[str], t0: str | None, t1: str | None) -> dict | None:
    """The nearest named place in time to a span of wall times: ``{label, confidence, near_s}``."""
    if not times or not t0:
        return None
    t1 = t1 or t0
    i = bisect_left(times, t0)
    j = bisect_right(times, t1)
    if i < j:  # a named point inside the span
        return {"label": Counter(names[i:j]).most_common(1)[0][0], "confidence": 0.4, "near_s": 0}
    best = None
    for k, ref in ((i - 1, t0), (j, t1)):
        if 0 <= k < len(times):
            try:
                gap = abs((datetime.fromisoformat(times[k]) - datetime.fromisoformat(ref[:19])).total_seconds())
            except ValueError:
                continue
            if best is None or gap < best[0]:
                best = (gap, names[k])
    if best is None:
        return None
    return {"label": best[1], "confidence": round(0.35 * 0.5 ** (best[0] / NEAR_HALF_LIFE_S), 3),
            "near_s": int(best[0])}


def _trip_rail(shoots: list[dict]) -> dict | None:
    """A trip's rail label: the place most of its photos were at (own places), else its nearest "Near" place."""
    own: Counter = Counter()
    conf: dict[str, list[tuple[float, int]]] = defaultdict(list)
    for sh in shoots:
        r = sh.get("rail")
        if r and r["near_s"] is None:
            own[r["label"]] += sh["groups"]
            conf[r["label"]].append((r["confidence"], sh["groups"]))
    if own:
        label = own.most_common(1)[0][0]
        w = sum(n for _, n in conf[label])
        return {"label": label, "confidence": round(sum(c * n for c, n in conf[label]) / w, 3), "near_s": None}
    near = [sh["rail"] for sh in shoots if sh.get("rail")]
    return min(near, key=lambda r: r["near_s"]) if near else None


def _saved_place(b: dict) -> str | None:
    """The place the user saved on this shoot's photos (live from the catalog, D-147), most photos first: it beats the
    plan's suggestion at once, before the next re-plan."""
    c = b.get("saved")
    return c.most_common(1)[0][0] if c else None


# How much a shoot or trip name says when the plan has no place or subject confidence (D-146): a name built from the
# date and camera says nothing (gray); a folder name a little; a phone place more.
NAME_SOURCE_CONFIDENCE = {"user": 1.0, "folder": 0.5, "phone": 0.6, "meta": 0.6, "fallback": 0.0}


def _name_confidence(name: Any) -> float | None:
    src = name.get("source") if isinstance(name, dict) else None
    return NAME_SOURCE_CONFIDENCE.get(src) if isinstance(src, str) else None


def outline(conn: sqlite3.Connection, plan: dict | None, *, volume_id: str | None = None,
            missing: str | None = None) -> dict:
    """``{"trips": [...], "items": [...], "plan_id", "frames"}``; see the module doc and rv-46 in the revamp spec.

    ``items`` is the whole timeline newest first: trips, shoots with no trip, and loose days
    (``{"kind": "day", "day", "groups"}``). Inside a trip the shoots run oldest first. With ``volume_id`` each entry
    also carries ``files`` and ``bytes`` on that drive, and the result has ``other`` (files with no photo group).
    """
    rows = _scope(conn, volume_id, missing)
    wall = repo.offset_shoot_wall_dates(conn)
    pindex = proposal_index(plan)
    files = _file_counts(conn, volume_id) if volume_id else None

    # --- accepted shoots and their trips
    db_shoots = {r["shoot_id"]: r for r in conn.execute(
        """SELECT s.shoot_id, s.name, s.name_source, s.shoot_date, s.place, s.subject, s.trip_id, t.name AS trip_name,
                  t.start_date AS trip_start, t.end_date AS trip_end
           FROM shoots s LEFT JOIN trips t ON t.trip_id = s.trip_id""")}
    labels = {v["volume_id"]: v["label"] for v in repo.list_volumes(conn)}
    acc_vols: dict[int, dict[str, int]] = defaultdict(dict)
    if db_shoots:
        for r in conn.execute(
            """SELECT g.shoot_id AS sid, i.volume_id AS vid, COUNT(DISTINCT g.group_id) AS n FROM capture_groups g
               JOIN assets a ON a.group_id = g.group_id AND a.group_role = 'primary'
               JOIN instances i ON i.asset_id = a.asset_id AND i.state IN ('indexed', 'verified')
               WHERE g.shoot_id IS NOT NULL GROUP BY g.shoot_id, i.volume_id"""):
            acc_vols[r["sid"]][r["vid"]] = int(r["n"])

    # --- bucket every in-scope group
    buckets: dict[str, dict] = {}
    loose: dict[str, int] = defaultdict(int)
    loose_gids: dict[str, list[int]] = defaultdict(list)
    for r in rows:
        gid = int(r["group_id"])
        ct = r["capture_time"]
        day = wall.get(gid) or (ct[:10] if ct else UNDATED)
        if r["shoot_id"] is not None and r["shoot_id"] in db_shoots:
            key = f"s{r['shoot_id']}"
        elif r["shoot_id"] is None and gid in pindex:
            key = pindex[gid]
        else:
            loose[day] += 1
            loose_gids[day].append(gid)
            continue
        b = buckets.setdefault(key, {"gids": [], "days": defaultdict(int), "bodies": set(), "times": [],
                                     "saved": Counter()})
        b["gids"].append(gid)
        saved = r["saved_place"] or r["saved_city"]
        if saved:
            b["saved"][saved] += 1
        b["days"][day] += 1
        if r["body"]:
            b["bodies"].add(r["body"])
        if ct:
            b["times"].append(ct)

    def common(key: str, b: dict) -> dict:
        out = {"key": key, "groups": len(b["gids"]), "bodies": sorted(b["bodies"]),
               "days": [{"day": d, "groups": n}
                        for d, n in sorted(b["days"].items(), key=lambda x: (x[0] == UNDATED, x[0]))],
               "_times": sorted(b["times"])}
        if files is not None:
            out["files"] = sum(files.get(g, (0, 0))[0] for g in b["gids"])
            out["bytes"] = sum(files.get(g, (0, 0))[1] for g in b["gids"])
        return out

    shoots: list[dict] = []
    for key, b in buckets.items():
        t = common(key, b)
        if key.startswith("s") and key[1:].isdigit():
            sid = int(key[1:])
            d = db_shoots[sid]
            vols = [{"volume_id": v, "label": labels.get(v) or "Drive", "frames": n}
                    for v, n in sorted(acc_vols.get(sid, {}).items(), key=lambda x: -x[1])]
            times = t["_times"]
            # an applied shoot whose place or subject the user has not set shows the planner's suggestion (D-145),
            # shaded by its confidence; set fields count as confidence 1
            det = next((x.get("details") or {} for x in (plan or {}).get("shoots") or []
                        if x.get("locked") and x.get("shoot_id") == sid), {})
            saved = _saved_place(b)
            place = d["place"] or saved or _text(det.get("place"))
            subject = d["subject"] or _text(det.get("subject"))
            guessed = [det[k] for k, own in (("place", d["place"] or saved), ("subject", d["subject"]))
                       if not own and isinstance(det.get(k), dict) and det[k].get("value")]
            conf = round(min(float(g.get("confidence") or 0) for g in guessed), 3) if guessed else 1.0
            if not guessed and not d["place"] and not d["subject"] and d["name_source"] == "fallback":
                conf = 0.0  # applied with only a date-and-camera name: shown gray, as a guess
                guessed = [{}]
            pconf = 1.0 if (d["place"] or saved) else float((det.get("place") or {}).get("confidence") or 0)
            t["_rail"] = (_short(place), pconf) if place else None
            shoots.append({**t, "shoot_id": sid, "proposal_id": None, "state": "accepted", "confidence": conf,
                           "suggested": bool(guessed),
                           "title": title_of(place, subject, d["name"]), "name": d["name"],
                           "place": place, "subject": subject, "shoot_date": d["shoot_date"],
                           "wall": {"start": times[0] if times else None, "end": times[-1] if times else None},
                           "volumes": vols, "sample_group_ids": _spread(sorted(b["gids"])),
                           "_trip": f"t{d['trip_id']}" if d["trip_id"] else None})
        else:
            ps = _plan_shoot(plan, key)
            det = ps.get("details") or {}
            saved = _saved_place(b)
            place, subject = saved or _text(det.get("place")), _text(det.get("subject"))
            if saved:  # the user's saved place is certain; the subject still carries its own confidence
                det = {**det, "place": {"value": saved, "confidence": 1.0}}
            name = _text(ps.get("name")) or "Shoot"
            keep = set(b["gids"])
            samples = [g for g in ps.get("sample_group_ids") or [] if g in keep][:8] or _spread(sorted(keep))
            pconf = float((det.get("place") or {}).get("confidence") or 0) if place else 0.0
            t["_rail"] = (_short(place), pconf) if place else None
            shoots.append({**t, "shoot_id": None, "proposal_id": key, "state": "proposed",
                           "confidence": (_confidence(det) if _confidence(det) is not None
                                          else _name_confidence(ps.get("name"))),
                           "title": title_of(place, subject, name), "name": name, "place": place,
                           "subject": subject, "shoot_date": ps.get("shoot_date"),
                           "wall": ps.get("wall") or {"start": None, "end": None},
                           "volumes": [{"volume_id": v.get("volume_id"), "label": v.get("label") or "Drive",
                                        "frames": v.get("frames", 0)} for v in ps.get("volumes") or []],
                           "sample_group_ids": samples, "_trip": ps.get("trip_id")})

    # --- the rail's label: a place, never a subject or folder name (D-148): the shoot's own place, else the nearest
    # named place in time ("Near …"), shaded by confidence
    times, names = _named_points(conn) if shoots else ([], [])
    for sh in shoots:
        own = sh.pop("_rail", None)
        if own:
            sh["rail"] = {"label": own[0], "confidence": round(own[1], 3), "near_s": None}
        else:
            w = sh.get("wall") or {}
            sh["rail"] = _near(times, names, (w.get("start") or "")[:19] or None, (w.get("end") or "")[:19] or None)

    # --- trips
    plan_trips = {t["proposal_id"]: t for t in (plan or {}).get("trips") or []}
    trips: dict[str, dict] = {}
    for s in shoots:
        tk = s.pop("_trip")
        s.pop("_times")
        s["trip_key"] = None
        if tk is None:
            continue
        if tk.startswith("t") and tk[1:].isdigit():
            d = db_shoots[s["shoot_id"]]
            tr = trips.setdefault(tk, {"key": tk, "name": d["trip_name"], "state": "accepted",
                                       "start_date": d["trip_start"], "end_date": d["trip_end"], "shoots": []})
        else:
            p = plan_trips.get(tk)
            if p is None:
                continue
            tr = trips.setdefault(tk, {"key": tk, "name": _text(p.get("name")) or "Trip", "state": "proposed",
                                       "start_date": p.get("start_date"), "end_date": p.get("end_date"),
                                       "shoots": []})
        tr["shoots"].append(s)
        s["trip_key"] = tk
    for tr in trips.values():
        tr["shoots"].sort(key=lambda s: (s["wall"].get("start") or s["shoot_date"] or "", s["key"]))
        tr["groups"] = sum(s["groups"] for s in tr["shoots"])
        if files is not None:
            tr["files"] = sum(s["files"] for s in tr["shoots"])
            tr["bytes"] = sum(s["bytes"] for s in tr["shoots"])
        known = [(s["confidence"], s["groups"]) for s in tr["shoots"] if s["confidence"] is not None]
        weight = sum(n for _, n in known)
        tr["confidence"] = (1.0 if tr["state"] == "accepted" else
                            round(sum(c * n for c, n in known) / weight, 3) if weight else None)
        tr["rail"] = _trip_rail(tr["shoots"])
        if tr["state"] == "proposed" and (plan_trips.get(tr["key"]) or {}).get("name", {}).get("source") == "fallback":
            tr["confidence"] = 0.0  # a trip named only by its date ("2021-04-14 trip") says nothing: gray
        dates = [s["shoot_date"] for s in tr["shoots"] if s["shoot_date"]]
        tr["start_date"] = tr["start_date"] or (min(dates) if dates else None)
        tr["end_date"] = tr["end_date"] or (max(dates) if dates else None)

    # --- the timeline
    items: list[dict] = [{"kind": "trip", **tr} for tr in trips.values()]
    items += [{"kind": "shoot", **s} for s in shoots if s["trip_key"] is None]
    for day, n in loose.items():
        it: dict = {"kind": "day", "key": f"{LOOSE_PREFIX}{day}", "day": day, "groups": n}
        if files is not None:
            it["files"] = sum(files.get(g, (0, 0))[0] for g in loose_gids[day])
            it["bytes"] = sum(files.get(g, (0, 0))[1] for g in loose_gids[day])
        items.append(it)

    def sort_key(it: dict) -> tuple:
        k = it["kind"]
        d = it["day"] if k == "day" else (it.get("end_date") if k == "trip" else it.get("shoot_date"))
        return (d is not None and d != UNDATED, d or "", it["key"])

    items.sort(key=sort_key, reverse=True)
    result: dict = {"plan_id": (plan or {}).get("plan_id"), "frames": len(rows),
                    "trips": sorted(trips.values(), key=lambda t: (t["end_date"] or "", t["key"]), reverse=True),
                    "items": items}
    if files is not None:
        grouped = {int(r["group_id"]) for r in rows}
        result["other"] = {"files": sum(v[0] for k, v in files.items() if k is None or k not in grouped),
                           "bytes": sum(v[1] for k, v in files.items() if k is None or k not in grouped)}
    return result


def resolve_key(conn: sqlite3.Connection, plan: dict | None, key: str, *, volume_id: str | None = None,
                missing: str | None = None) -> list[int]:
    """The group ids a shoot key stands for, in scope: ``s<id>`` (applied shoot), a proposal id (the plan's unapplied
    members) or ``loose:<day>`` (wall day, no shoot, in no proposal). Raises ``ValueError`` for an unknown key."""
    where, params = repo._group_filter_sql(volume_id=volume_id, shoot_id=None, missing=missing)
    base = "SELECT g.group_id FROM capture_groups g LEFT JOIN group_meta m ON m.group_id = g.group_id WHERE " + where
    if key.startswith(LOOSE_PREFIX):
        day = key[len(LOOSE_PREFIX):]
        pindex = proposal_index(plan)
        wall = repo.offset_shoot_wall_dates(conn)
        if day == UNDATED:
            rows = conn.execute(base + " AND g.shoot_id IS NULL AND g.capture_time IS NULL", params).fetchall()
        else:
            rows = conn.execute(base + " AND g.shoot_id IS NULL AND substr(g.capture_time, 1, 10) = ?",
                                params + [day]).fetchall()
        return [int(r[0]) for r in rows if int(r[0]) not in pindex and wall.get(int(r[0]), day) == day]
    if key.startswith("s") and key[1:].isdigit():
        return [int(r[0]) for r in conn.execute(base + " AND g.shoot_id = ?", params + [int(key[1:])])]
    ps = _plan_shoot(plan, key)
    if not ps:
        raise ValueError(f"unknown shoot key {key!r}")
    ids = [int(g) for g in ps.get("group_ids") or []]
    if not ids:
        return []
    return [int(r[0]) for r in conn.execute(
        base + " AND g.shoot_id IS NULL AND g.group_id IN (SELECT value FROM json_each(?))",
        params + [json.dumps(ids)])]
