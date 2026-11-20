"""The trips outline (rv-46, D-134): every capture group placed in a trip, a shoot or a loose day.

Pure and read-only. A frame belongs to an **accepted** shoot when ``capture_groups.shoot_id`` is set (its trip comes
from ``shoots.trip_id``); otherwise to a **proposed** shoot when the newest plan's ``shoots[].group_ids`` holds it;
otherwise it is **loose** and listed by day. Decisions saved after the plan was made are not folded in (Re-plan does
that). ``outline`` answers the Photos and Files views; ``resolve_key`` turns a shoot key into its group ids.
"""

from __future__ import annotations

import json
import sqlite3
from collections import defaultdict
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
        f"""SELECT g.group_id, g.shoot_id, g.capture_time, b.nickname AS body
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
        b = buckets.setdefault(key, {"gids": [], "days": defaultdict(int), "bodies": set(), "times": []})
        b["gids"].append(gid)
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
            place = d["place"] or _text(det.get("place"))
            subject = d["subject"] or _text(det.get("subject"))
            guessed = [det[k] for k, own in (("place", d["place"]), ("subject", d["subject"]))
                       if not own and isinstance(det.get(k), dict) and det[k].get("value")]
            conf = round(min(float(g.get("confidence") or 0) for g in guessed), 3) if guessed else 1.0
            if not guessed and not d["place"] and not d["subject"] and d["name_source"] == "fallback":
                conf = 0.0  # applied with only a date-and-camera name: shown gray, as a guess
                guessed = [{}]
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
            place, subject = _text(det.get("place")), _text(det.get("subject"))
            name = _text(ps.get("name")) or "Shoot"
            keep = set(b["gids"])
            samples = [g for g in ps.get("sample_group_ids") or [] if g in keep][:8] or _spread(sorted(keep))
            shoots.append({**t, "shoot_id": None, "proposal_id": key, "state": "proposed",
                           "confidence": (_confidence(det) if _confidence(det) is not None
                                          else _name_confidence(ps.get("name"))),
                           "title": title_of(place, subject, name), "name": name, "place": place,
                           "subject": subject, "shoot_date": ps.get("shoot_date"),
                           "wall": ps.get("wall") or {"start": None, "end": None},
                           "volumes": [{"volume_id": v.get("volume_id"), "label": v.get("label") or "Drive",
                                        "frames": v.get("frames", 0)} for v in ps.get("volumes") or []],
                           "sample_group_ids": samples, "_trip": ps.get("trip_id")})

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
