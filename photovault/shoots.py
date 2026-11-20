"""The shoot planner (sh-1, D-080; review `docs/reviews/2026-11-18-sh-0-shoot-tagging.md` §§3-4).

`plan` reads the catalog and returns a dry-run proposal of trips and shoots in the plan-file format of
`docs/specs/shoots/tasks.md`; it writes nothing. `calibrate` scores the boundary step against the date-prefixed
folders. `write_plan` writes one JSON file under ``<home>/plans``. The review decisions (sh-2) are rows in
``shoot_plan_decisions``: `add_decisions`, `list_decisions` and `delete_decisions` are the only writers of the catalog
here, and they touch that table only.

Every comparison uses the instant (`timemodel`); the wall clock gives the shoot date, display and names.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import unicodedata
from bisect import bisect_left, bisect_right
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from . import naming
from .catalog import repo
from . import shoot_density, shoot_places, shoot_scoring
from .timemodel import BodyClocks, instant_info, offset_tz

PHONE_SOURCES = ("iphone", "ipad", "glasses")
PHONE_MATCH_S = 2 * 3600
SPAN_PAD_S = 1800
QUICK_GAP_S = 900          # never split between frames closer than this
SPLIT_GAP_S = 3600         # a location split needs a gap above this

_DATED_RE = re.compile(r"^(\d{4})[-_.]?(\d{2})[-_.]?(\d{2})\s*[-–—_.:]*\s*(.+)$")
_BARE_DATE_RE = re.compile(r"^\d{4}([-_. ]?\d{2}){0,2}$")
_BUILTIN_STOP = [
    re.compile(p, re.I) for p in (
        r"^dcim$", r"^\d{3}nc\w+$", r"^\d{3}[a-z]{3}\w*$", r"^card\s*\d*$", r"^card export$", r"^camera imports?$",
        r"^imports?$", r"^exports?$", r"^raw$", r"^selected$", r"^selects?$", r"^photos?$", r"^pictures$",
        r"^100apple$", r"^misc$", r"^new$", r"^untitled( folder)?$", r"^temp$", r"^tmp$", r"^backups?$", r".* backups?$",
        r"^edits?$", r"^final$", r"^\d+(\.\d+)?\s*[gt]b$", r"^(nikon|canon|sony|fuji|fujifilm|gopro|dji|apple|iphone)\b.*",
        r"^z\s?\d{1,2}\s?(ii|iii)?$", r"^d\d{3,4}[a-z]?$")
]


# ---------------------------------------------------------------- small helpers

def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371.0 * 2 * math.asin(min(1.0, math.sqrt(a)))


def clean_name(text: str) -> str:
    """Drop private-use and control glyphs, normalise to NFC, collapse spaces."""
    text = unicodedata.normalize("NFC", text)
    text = "".join(c for c in text if unicodedata.category(c) not in ("Co", "Cc", "Cf", "Cs", "Cn"))
    return re.sub(r"\s+", " ", text).strip(" -_.,")


def _sha(prefix: str, first: int, last: int) -> str:
    return prefix + hashlib.sha1(f"{first}-{last}".encode()).hexdigest()[:16]


def _mode(values) -> Any:
    c = Counter(v for v in values if v)
    return c.most_common(1)[0][0] if c else None


def _fmt_off(seconds: int) -> str:
    sign = "+" if seconds >= 0 else "-"
    seconds = abs(seconds)
    return f"{sign}{seconds // 3600:02d}:{seconds % 3600 // 60:02d}"


def _is_container(name: str, stop: set[str]) -> bool:
    low = name.lower()
    return (low in stop or _BARE_DATE_RE.match(name) is not None or any(p.match(name) for p in _BUILTIN_STOP)
            or shoot_scoring.is_generic_name(name)
            or not any(ch.isalpha() for ch in name))


def folder_info(rel_path: str | None, stop: set[str]) -> tuple[tuple[str, str] | None, list[str]]:
    """``(dated, humans)`` for a path: the deepest ``YYYYMMDD - Name`` folder as ``(path-key, name)``, and the other
    human folder names (deepest first) that are not containers or stoplisted."""
    if not rel_path:
        return None, []
    parts = [clean_name(p) for p in rel_path.split("/")[:-1]]
    dated = None
    humans: list[str] = []
    for i in range(len(parts) - 1, -1, -1):
        part = parts[i]
        if not part:
            continue
        m = _DATED_RE.match(part)
        if m and any(ch.isalpha() for ch in m.group(4)):
            if dated is None:
                dated = ("/".join(parts[:i + 1]), clean_name(m.group(4)))
            continue
        if not _is_container(part, stop):
            humans.append(part)
    return dated, humans


# ---------------------------------------------------------------- loading

class Item:
    __slots__ = ("gid", "when", "ts", "body", "seq", "locked", "shoot_id", "cap", "off", "rel", "lat", "lon",
                 "city", "state", "place", "dated", "humans", "prov")


class Phone:
    """Phone points as parallel arrays, for nearest-in-time lookups."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        marks = ",".join("?" * len(PHONE_SOURCES))
        try:
            rows = conn.execute(
                f"SELECT g.utc_time, g.lat, g.lon, g.local_offset, g.place_name, g.city, g.state, g.country, "
                f"a.caption, a.labels_json, a.persons_json, a.hints_json FROM geo_points g "
                f"LEFT JOIN phone_analysis a ON a.point_id = g.point_id WHERE g.source IN ({marks})",
                PHONE_SOURCES).fetchall()
        except sqlite3.OperationalError:  # before schema V9
            rows = conn.execute(
                f"SELECT utc_time, lat, lon, local_offset, place_name, city, state, country, NULL AS caption, "
                f"NULL AS labels_json, NULL AS persons_json, NULL AS hints_json FROM geo_points "
                f"WHERE source IN ({marks})", PHONE_SOURCES).fetchall()
        pts = []
        for r in rows:
            try:
                t = datetime.fromisoformat(r["utc_time"])
            except ValueError:
                continue
            if t.tzinfo is None:
                t = t.replace(tzinfo=UTC)
            ana = ((r["caption"], r["labels_json"], r["persons_json"], r["hints_json"])
                   if r["labels_json"] is not None or r["caption"] is not None or r["hints_json"] is not None else None)
            pts.append((t.timestamp(), r["lat"], r["lon"], r["local_offset"], r["place_name"], r["city"],
                        r["state"], r["country"], ana))
        pts.sort(key=lambda p: p[0])
        self.pts = pts
        self.ts = [p[0] for p in pts]
        self.home = _mode(p[3] for p in pts)

    def nearest(self, ts: float, within_s: float = PHONE_MATCH_S):
        if not self.ts:
            return None
        i = bisect_left(self.ts, ts)
        best, best_d = None, within_s + 1
        for j in (i - 1, i):
            if 0 <= j < len(self.ts) and abs(self.ts[j] - ts) < best_d:
                best, best_d = self.pts[j], abs(self.ts[j] - ts)
        return best if best_d <= within_s else None

    def span(self, lo: float, hi: float):
        return self.pts[bisect_left(self.ts, lo - SPAN_PAD_S):bisect_right(self.ts, hi + SPAN_PAD_S)]


def _stoplist(conn: sqlite3.Connection, extra: list[str] | None = None) -> set[str]:
    raw = repo.get_setting(conn, "shoot_name_stoplist", "[]")
    try:
        items = json.loads(raw) if raw else []
    except ValueError:
        items = []
    names = [*items, *(extra or [])]
    try:
        for r in conn.execute("SELECT model, nickname FROM bodies"):
            names += [r["model"], r["nickname"]]
        names += [r["label"] for r in conn.execute("SELECT label FROM volumes")]
    except sqlite3.OperationalError:
        pass
    return {clean_name(str(x)).lower() for x in names if x}


def load_items(conn: sqlite3.Connection, stop: set[str], phone: Phone) -> tuple[list[Item], int]:
    """All groups with a capture time, sorted by instant, plus the count without one."""
    default_offset = repo.get_setting(conn, "default_offset", "+00:00")
    clocks = BodyClocks.load(conn)
    nickname = {r["body_id"]: r["nickname"] for r in conn.execute("SELECT body_id, nickname FROM bodies")}
    cur = conn.execute(
        """SELECT g.group_id, g.body_id, g.capture_time, g.capture_offset, g.shoot_id, g.sequence_id,
                  COALESCE(m.time_shift_s, 0) AS time_shift_s, m.lat, m.lon, m.city, m.state, m.place_name,
                  i.rel_path, (v.role = 'WORKING') AS working
           FROM capture_groups g
           LEFT JOIN group_meta m ON m.group_id = g.group_id
           LEFT JOIN assets a ON a.group_id = g.group_id AND a.group_role = 'primary'
           LEFT JOIN instances i ON i.asset_id = a.asset_id AND i.state IN ('indexed', 'verified')
           LEFT JOIN volumes v ON v.volume_id = i.volume_id
           ORDER BY g.group_id""")
    items: dict[int, Item] = {}
    best_pref: dict[int, int] = {}
    unplaced = 0
    seen_none: set[int] = set()
    for r in cur:
        gid = r["group_id"]
        it = items.get(gid)
        if it is None:
            if gid in seen_none:
                continue
            info = instant_info(r, default_offset, clocks)
            if info is None:
                seen_none.add(gid)
                unplaced += 1
                continue
            it = Item()
            it.gid, it.when, it.ts = gid, info.when, info.when.timestamp()
            it.prov = info.provenance
            it.body = nickname.get(r["body_id"]) or "?"
            it.seq, it.shoot_id, it.cap, it.off = r["sequence_id"], r["shoot_id"], r["capture_time"], r["capture_offset"]
            it.locked = r["shoot_id"] is not None
            it.rel = None
            it.city, it.state, it.place = r["city"], r["state"], r["place_name"]
            if r["lat"] is not None and r["lon"] is not None:
                it.lat, it.lon = r["lat"], r["lon"]
            else:
                p = phone.nearest(it.ts)
                it.lat, it.lon = (p[1], p[2]) if p else (None, None)
            it.dated, it.humans = None, []
            items[gid] = it
            best_pref[gid] = -1
        if r["rel_path"] is not None and (pref := 2 if r["working"] else 1) > best_pref[gid]:
            best_pref[gid] = pref
            it.rel = r["rel_path"]
    out = sorted(items.values(), key=lambda x: (x.ts, x.gid))
    for it in out:
        it.dated, it.humans = folder_info(it.rel, stop)
    return out, unplaced


# ---------------------------------------------------------------- boundaries

def segments(items: list[Item], gap_h: float, split_km: float) -> list[tuple[int, int]]:
    """Index ranges ``(lo, hi)`` inclusive of the shoots, over a time-sorted item list."""
    n = len(items)
    if not n:
        return []
    gap_s = gap_h * 3600

    def same_seq(k: int) -> bool:
        return items[k].seq is not None and items[k].seq == items[k + 1].seq

    cands: list[tuple[int, int]] = []
    lo = 0
    for k in range(n - 1):
        if items[k + 1].ts - items[k].ts >= gap_s and not same_seq(k):
            cands.append((lo, k))
            lo = k + 1
    cands.append((lo, n - 1))

    out: list[tuple[int, int]] = []
    for a, b in cands:
        start = a
        last = None  # index of the previous located item in this candidate
        for j in range(a, b + 1):
            if items[j].lat is None:
                continue
            if last is not None and haversine_km(items[last].lat, items[last].lon, items[j].lat, items[j].lon) > split_km:
                best, best_gap = None, 0.0
                for k in range(max(last, start), j):
                    g = items[k + 1].ts - items[k].ts
                    if g > SPLIT_GAP_S and g >= QUICK_GAP_S and g > best_gap and not same_seq(k):
                        best, best_gap = k, g
                if best is not None:
                    out.append((start, best))
                    start = best + 1
            last = j
        out.append((start, b))
    return out


# ---------------------------------------------------------------- shoot building

def _evenly(seq: list, n: int) -> list:
    if len(seq) <= n:
        return list(seq)
    return [seq[round(i * (len(seq) - 1) / (n - 1))] for i in range(n)]


def _max_extent_km(pts: list[tuple[float, float]]) -> float:
    if len(pts) < 2:
        return 0.0
    pts = _evenly(pts, 200)
    far = max(pts, key=lambda p: haversine_km(pts[0][0], pts[0][1], p[0], p[1]))
    return max(haversine_km(far[0], far[1], p[0], p[1]) for p in pts)


def _part_of_day(hour: int) -> str:
    return "morning" if 5 <= hour < 12 else "afternoon" if 12 <= hour < 17 else "evening" if 17 <= hour < 22 else "night"


def _build_shoot(seg: list[Item], phone: Phone, default_offset: str,
                 stop: set[str], rejected: set[str]) -> dict:
    first, last = seg[0], seg[-1]
    camera_off = _mode(i.off for i in seg) or default_offset
    pts_phone = phone.span(first.ts, last.ts)

    # local_offset proposal
    offs = [p[3] for p in pts_phone if p[3]]
    proposal = None
    tz_change = False
    if offs:
        top, count = Counter(offs).most_common(1)[0]
        if count / len(offs) >= 0.8 and top != camera_off:
            try:
                same = offset_tz(top).utcoffset(None) == offset_tz(camera_off).utcoffset(None)
            except ValueError:
                same = False
            proposal = None if same else top
        shares = Counter(offs)
        if proposal or sum(1 for v in shares.values() if v / len(offs) >= 0.2) >= 2:
            tz_change = True
    wall_off = proposal or camera_off
    tz = offset_tz(wall_off) if proposal else offset_tz(camera_off)

    def wall(it: Item) -> datetime:
        if proposal:
            return it.when.astimezone(tz).replace(tzinfo=None)
        return datetime.fromisoformat(str(it.cap)[:19])

    w0, w1 = wall(first), wall(last)
    iso_off = _fmt_off(int(tz.utcoffset(None).total_seconds()))

    located = [(i.lat, i.lon) for i in seg if i.lat is not None]
    centroid = ({"lat": sum(p[0] for p in located) / len(located), "lon": sum(p[1] for p in located) / len(located)}
                if located else None)
    gaps = sorted(((seg[k + 1].ts - seg[k].ts, seg[k].gid) for k in range(len(seg) - 1)), reverse=True)[:20]
    gaps = [{"after_group_id": g, "gap_s": int(s)} for s, g in gaps if s > 0]
    by_body = Counter(i.body for i in seg)

    # flags
    flags: list[str] = []
    if _max_extent_km(located) > 25:
        flags.append("multi_place")
    if tz_change:
        flags.append("tz_change")
    med = {}
    for body in by_body:
        ts = sorted(i.ts for i in seg if i.body == body)
        if len(ts) >= 3:
            med[body] = ts[len(ts) // 2]
    names = sorted(med)
    mismatch = False
    for x in range(len(names)):
        for y in range(x + 1, len(names)):
            d = abs(med[names[x]] - med[names[y]])
            if d >= 3000 and abs(d - round(d / 3600) * 3600) <= 300:
                mismatch = True
    if mismatch:
        flags.append("body_clock_mismatch")
    if len(seg) >= 1000:
        ds = sorted(seg[k + 1].ts - seg[k].ts for k in range(len(seg) - 1))
        if ds and ds[len(ds) // 2] <= 10:
            flags.append("huge_timelapse")

    # names, best first
    cands: list[tuple[str, str]] = []
    dated_counts = Counter(i.dated[1] for i in seg if i.dated)
    if dated_counts:
        nm, c = dated_counts.most_common(1)[0]
        if c / len(seg) >= 0.5:
            cands.append((nm, "folder"))
    place = _mode([*(p[4] for p in pts_phone), *(i.place for i in seg)])
    city = _mode([*(p[5] for p in pts_phone), *(i.city for i in seg)])
    if place:
        cands.append((clean_name(place), "phone"))
    if city:
        cands.append((clean_name(city), "phone"))
    other = Counter(h for i in seg for h in i.humans[:1])
    cands += [(h, "folder_other") for h, _ in other.most_common(3)]
    cands += [(nm, "folder_other") for nm, _ in dated_counts.most_common(3) if (nm, "folder") not in cands]
    shoot_date = naming.shoot_date(w0).isoformat()
    cands.append((f"{shoot_date} · {first.body}", "fallback"))
    seen: set[str] = set()
    uniq: list[tuple[str, str]] = []
    for value, src in cands:
        low = value.lower()
        if not value or low in seen or low in rejected or (src == "folder_other" and low in stop):
            continue
        seen.add(low)
        uniq.append((value, src))
    if not uniq:
        uniq = [(f"{shoot_date} · {first.body}", "fallback")]
    top_val, top_src = uniq[0]
    weak = top_src in ("fallback", "folder_other")
    if weak:
        flags.append("weak_name")
    export = lambda s: "folder" if s == "folder_other" else s  # noqa: E731
    name = {"value": top_val, "source": export(top_src),
            "alternatives": [{"value": v, "source": export(s)} for v, s in uniq[1:]]}

    gids = [i.gid for i in seg]
    return {
        "proposal_id": _sha("s:", gids[0], gids[-1]), "trip_id": None, "name": name,
        "shoot_date": shoot_date,
        "start_instant": first.when.isoformat(timespec="seconds"), "end_instant": last.when.isoformat(timespec="seconds"),
        "wall": {"start": w0.isoformat() + iso_off, "end": w1.isoformat() + iso_off},
        "local_offset": ({"value": proposal, "camera": camera_off, "source": "phone", "changes": True} if proposal
                         else {"value": camera_off, "camera": camera_off, "source": "camera", "changes": False}),
        "group_ids": gids,
        "counts": {"frames": len(seg), "by_body": dict(by_body), "located": len(located)},
        "sample_group_ids": [g for g in _evenly(gids, 12)],
        "centroid": centroid,
        "points": [[round(a, 5), round(b, 5)] for a, b in _evenly(located, 50)],
        "gaps": gaps, "flags": flags, "locked": False,
        # private bookkeeping, removed before output
        "_phone_off": _mode(offs), "_state": _mode(p[6] for p in pts_phone) or _mode(i.state for i in seg),
        "_country": _mode(p[7] for p in pts_phone), "_humans": {h for i in seg for h in i.humans},
        "_t0": first.ts, "_t1": last.ts, "_w0": w0, "_w1": w1, "_body": first.body, "_items": seg,
    }


def _locked_shoots(conn: sqlite3.Connection, items: list[Item]) -> list[dict]:
    meta = {r["shoot_id"]: r for r in conn.execute("SELECT * FROM shoots")}
    by: dict[int, list[Item]] = {}
    for it in items:
        if it.locked:
            by.setdefault(it.shoot_id, []).append(it)
    out = []
    for sid, seg in by.items():
        r = meta.get(sid)
        gids = [i.gid for i in seg]
        by_body = Counter(i.body for i in seg)
        located = [(i.lat, i.lon) for i in seg if i.lat is not None]
        lo = r["local_offset"] if r is not None and "local_offset" in r.keys() else None
        cam = _mode(i.off for i in seg) or "+00:00"
        out.append({
            "proposal_id": _sha("s:", gids[0], gids[-1]), "trip_id": None,
            "name": {"value": r["name"] if r else f"shoot {sid}",
                     "source": (r["name_source"] if r is not None and r["name_source"] else "user"), "alternatives": []},
            "shoot_date": r["shoot_date"] if r else str(seg[0].cap)[:10],
            "start_instant": seg[0].when.isoformat(timespec="seconds"), "end_instant": seg[-1].when.isoformat(timespec="seconds"),
            "wall": {"start": str(seg[0].cap)[:19], "end": str(seg[-1].cap)[:19]},
            "local_offset": {"value": lo or cam, "camera": cam, "source": "shoot" if lo else "camera", "changes": False},
            "group_ids": gids,
            "counts": {"frames": len(seg), "by_body": dict(by_body), "located": len(located)},
            "sample_group_ids": _evenly(gids, 12),
            "centroid": ({"lat": sum(p[0] for p in located) / len(located), "lon": sum(p[1] for p in located) / len(located)}
                         if located else None),
            "points": [[round(a, 5), round(b, 5)] for a, b in _evenly(located, 50)],
            "gaps": [], "flags": [], "locked": True, "shoot_id": sid, "_t0": seg[0].ts, "_t1": seg[-1].ts,
            "_w0": datetime.fromisoformat(str(seg[0].cap)[:19]), "_w1": datetime.fromisoformat(str(seg[-1].cap)[:19]),
            "_items": seg,
            "_db_trip": r["trip_id"] if r is not None and "trip_id" in r.keys() else None,
        })
    return out


# ---------------------------------------------------------------- trips

def _date_range(d0: str, d1: str) -> str:
    a, b = datetime.fromisoformat(d0), datetime.fromisoformat(d1)
    if a == b:
        return f"{a.day} {a:%b}"
    if a.year != b.year:
        return f"{a.day} {a:%b %Y}–{b.day} {b:%b %Y}"
    if a.month != b.month:
        return f"{a.day} {a:%b}–{b.day} {b:%b}"
    return f"{a.day}–{b.day} {a:%b}"


def _make_trips(shoots: list[dict], phone: Phone, merge_h: float, region_km: float) -> list[list[dict]]:
    groups: list[list[dict]] = []
    cur: list[dict] = []
    for s in shoots:
        if cur:
            p = cur[-1]
            ok = s["_t0"] - p["_t1"] <= merge_h * 3600
            if ok:
                near = (s["centroid"] and p["centroid"]
                        and haversine_km(s["centroid"]["lat"], s["centroid"]["lon"], p["centroid"]["lat"],
                                         p["centroid"]["lon"]) <= region_km)
                away = (phone.home and s["_phone_off"] and p["_phone_off"]
                        and s["_phone_off"] != phone.home and p["_phone_off"] != phone.home)
                shared = bool(s["_humans"] & p["_humans"])
                ok = bool(near or away or shared)
            if ok:
                cur.append(s)
                continue
            groups.append(cur)
        cur = [s]
    if cur:
        groups.append(cur)
    return [g for g in groups if len(g) >= 2]


def _locked_trips(conn: sqlite3.Connection, locked: list[dict]) -> list[dict]:
    """Applied trips (sh-4), shown read-only like their locked shoots; sets each member's ``trip_id``."""
    try:
        names = {r["trip_id"]: r["name"] for r in conn.execute("SELECT trip_id, name FROM trips")}
    except sqlite3.OperationalError:
        return []
    by: dict[int, list[dict]] = {}
    for s in locked:
        if s.get("_db_trip") in names:
            by.setdefault(s["_db_trip"], []).append(s)
    out = []
    for tid, members in by.items():
        members.sort(key=lambda s: s["_t0"])
        t = _trip_dict(members, _sha("t:", members[0]["group_ids"][0], members[-1]["group_ids"][-1]))
        t["name"] = {"value": names[tid], "source": "user", "alternatives": []}
        t["locked"] = True
        for s in members:
            s["trip_id"] = t["proposal_id"]
        out.append(t)
    return out


def _trip_dict(members: list[dict], pid: str | None = None) -> dict:
    d0, d1 = members[0]["shoot_date"], max(s["shoot_date"] for s in members)
    rng = _date_range(d0, d1)
    region = _mode(s.get("_state") for s in members) or _mode(s.get("_country") for s in members)
    first_gid, last_gid = members[0]["group_ids"][0], members[-1]["group_ids"][-1]
    name = ({"value": f"{clean_name(region)} · {rng}", "source": "phone",
             "alternatives": [{"value": f"{d0} trip", "source": "fallback"}]} if region
            else {"value": f"{d0} trip", "source": "fallback", "alternatives": []})
    return {"proposal_id": pid or _sha("t:", first_gid, last_gid), "name": name, "start_date": d0, "end_date": d1,
            "shoot_ids": [s["proposal_id"] for s in members], "flags": []}


# ---------------------------------------------------------------- decisions

DECISION_KINDS = ("accept", "split", "merge", "skip", "rename", "reject_name", "move", "include", "exclude",
                  "detail")  # the V9 CHECK
DETAIL_FIELDS = ("place", "subject", "people")
_PAYLOAD_KEYS = {"accept": {"confirmed"}, "skip": set(), "split": {"after_group_id"}, "merge": {"with"},
                 "rename": {"value", "source"}, "reject_name": {"value"}, "move": {"trip_id"},
                 "include": {"session"}, "exclude": {"session"}, "detail": {"field", "value", "source"}}
# Every kind may also carry these: `batch` groups the rows of one review action ("Accept trip", "Accept all
# unflagged"), so they are listed and undone together; `ui` names that action and `trip` its trip.
_BATCH_KEYS = {"batch", "ui", "trip"}


def _load_payload(text: str | None) -> dict:
    try:
        payload = json.loads(text or "{}")
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _decisions(conn: sqlite3.Connection) -> list[dict]:
    try:
        rows = conn.execute(
            "SELECT decision_id, proposal_id, kind, payload_json FROM shoot_plan_decisions "
            "ORDER BY decision_id").fetchall()
    except sqlite3.OperationalError:
        return []
    return [{**_load_payload(r["payload_json"]), "id": r["proposal_id"], "kind": r["kind"],
             "decision_id": r["decision_id"]} for r in rows]


def check_decision(d: Any) -> tuple[str, str, dict]:
    """Validate one ``{proposal_id, kind, payload}``; raise ValueError with a plain message."""
    if not isinstance(d, dict):
        raise ValueError("a decision must be an object")
    pid, kind, payload = d.get("proposal_id"), d.get("kind"), d.get("payload", {})
    if not isinstance(pid, str) or not re.fullmatch(r"[st]:[0-9a-f]{6,40}", pid):
        raise ValueError("proposal_id must be a shoot (s:…) or trip (t:…) id")
    if kind not in DECISION_KINDS:
        raise ValueError(f"kind must be one of: {', '.join(DECISION_KINDS)}")
    if payload is None:
        payload = {}
    if not isinstance(payload, dict):
        raise ValueError("payload must be an object")
    extra = set(payload) - _PAYLOAD_KEYS[kind] - _BATCH_KEYS
    if extra:
        raise ValueError(f"{kind}: unexpected payload keys {', '.join(sorted(extra))}")
    if pid.startswith("t:") and kind != "rename":
        raise ValueError(f"{kind} applies to a shoot, not a trip")
    if kind == "split":
        g = payload.get("after_group_id")
        if isinstance(g, bool) or not isinstance(g, int):
            raise ValueError("split needs after_group_id (a whole number)")
    elif kind == "merge" and payload.get("with") not in ("prev", "next"):
        raise ValueError("merge needs with: prev or next")
    elif kind in ("rename", "reject_name"):
        v = payload.get("value")
        if not isinstance(v, str) or not v.strip() or len(v) > 200:
            raise ValueError(f"{kind} needs a value (1-200 characters)")
        if kind == "rename" and not isinstance(payload.get("source", "user"), str):
            raise ValueError("source must be text")
    elif kind == "move":
        t = payload.get("trip_id")
        if t is not None and not (isinstance(t, str) and re.fullmatch(r"t:[0-9a-f]{6,40}", t)):
            raise ValueError("move needs trip_id: a trip id or null")
    elif kind in ("include", "exclude"):
        sess = payload.get("session")
        if (not isinstance(sess, list) or len(sess) != 2
                or any(isinstance(g, bool) or not isinstance(g, int) for g in sess)):
            raise ValueError(f"{kind} needs session: [first_group_id, last_group_id]")
    elif kind == "detail":
        v = payload.get("value")
        if payload.get("field") not in DETAIL_FIELDS:
            raise ValueError(f"detail needs field: {', '.join(DETAIL_FIELDS)}")
        if payload["field"] == "people":
            if not isinstance(v, list) or any(not isinstance(x, str) for x in v) or len(v) > 100:
                raise ValueError("detail people needs value: a list of names")
        elif not isinstance(v, str) or not v.strip() or len(v) > 200:
            raise ValueError("detail needs a value (1-200 characters)")
        if not isinstance(payload.get("source", "user"), str):
            raise ValueError("source must be text")
    for k in ("batch", "ui", "trip"):
        if k in payload and not isinstance(payload[k], str):
            raise ValueError(f"{k} must be text")
    if "confirmed" in payload and payload["confirmed"] is not True:
        raise ValueError("confirmed must be true")
    return pid, kind, payload


def add_decisions(conn: sqlite3.Connection, decisions: list[Any], *, now: datetime | None = None) -> list[dict]:
    """Store review decisions in order, all or none. The caller holds the transaction."""
    checked = [check_decision(d) for d in decisions]
    at = (now or datetime.now(UTC)).isoformat(timespec="seconds")
    ids = []
    for pid, kind, payload in checked:
        cur = conn.execute("INSERT INTO shoot_plan_decisions (proposal_id, kind, payload_json, decided_at) "
                           "VALUES (?,?,?,?)", (pid, kind, json.dumps(payload, ensure_ascii=False), at))
        ids.append(cur.lastrowid)
    return [d for d in list_decisions(conn) if d["decision_id"] in set(ids)]


def list_decisions(conn: sqlite3.Connection, after: int = 0) -> list[dict]:
    """Decisions with an id above `after`, oldest first, as ``{decision_id, proposal_id, kind, payload, decided_at}``."""
    rows = conn.execute("SELECT decision_id, proposal_id, kind, payload_json, decided_at FROM shoot_plan_decisions "
                        "WHERE decision_id > ? ORDER BY decision_id", (after,)).fetchall()
    return [{"decision_id": r["decision_id"], "proposal_id": r["proposal_id"], "kind": r["kind"],
             "payload": _load_payload(r["payload_json"]), "decided_at": r["decided_at"]} for r in rows]


def delete_decisions(conn: sqlite3.Connection, ids: list[int]) -> int:
    """Undo: remove decisions by id. Returns how many went. The caller holds the transaction."""
    gone = 0
    for i in ids:
        gone += conn.execute("DELETE FROM shoot_plan_decisions WHERE decision_id = ?", (i,)).rowcount
    return gone


def _replay_structure(items: list[Item], segs: list[tuple[int, int]], decisions: list[dict]) -> list[tuple[int, int]]:
    pos = {it.gid: k for k, it in enumerate(items)}
    segs = list(segs)

    def find(pid: str) -> int | None:
        for idx, (a, b) in enumerate(segs):
            if _sha("s:", items[a].gid, items[b].gid) == pid:
                return idx
        return None

    for d in decisions:
        idx = find(d["id"]) if str(d["id"]).startswith("s:") else None
        if idx is None:
            continue
        a, b = segs[idx]
        if d["kind"] == "split":
            k = pos.get(d.get("after_group_id"))
            if k is not None and a <= k < b:
                segs[idx:idx + 1] = [(a, k), (k + 1, b)]
        elif d["kind"] == "merge":
            other = idx - 1 if d.get("with") == "prev" else idx + 1
            if 0 <= other < len(segs) and segs[min(idx, other)][1] + 1 == segs[max(idx, other)][0]:
                lo, hi = min(idx, other), max(idx, other)
                segs[lo:hi + 1] = [(segs[lo][0], segs[hi][1])]
    return segs


def _replay_members(free: list[Item], groups: list[list[Item]], base_pids: list[str],
                    decisions: list[dict]) -> tuple[list[list[Item]], list[Item], dict[str, set[int]]]:
    """Apply `include` and `exclude` decisions (sh-8): a session (frames from its first to its last group id, among
    those held by one shoot) moves into the shoot, or out of it into the unassigned pool. An excluded session is never
    offered to that shoot again. Returns the new groups, the unassigned items and the exclusions per proposal id."""
    pos = {it.gid: k for k, it in enumerate(free)}
    owner: list[int | None] = [None] * len(free)
    for gi, g in enumerate(groups):
        for it in g:
            owner[pos[it.gid]] = gi
    by_pid = {pid: gi for gi, pid in enumerate(base_pids)}
    excluded: dict[str, set[int]] = {}
    for d in decisions:
        if d["kind"] not in ("include", "exclude") or d["id"] not in by_pid:
            continue
        sess = d.get("session")
        if not (isinstance(sess, list) and len(sess) == 2 and sess[0] in pos and sess[1] in pos):
            continue
        a, b = sorted((pos[sess[0]], pos[sess[1]]))
        who = owner[a]
        frames = [k for k in range(a, b + 1) if owner[k] == who]
        gi = by_pid[d["id"]]
        if d["kind"] == "include":
            for k in frames:
                owner[k] = gi
                excluded.get(d["id"], set()).discard(free[k].gid)
        elif who == gi:
            for k in frames:
                owner[k] = None
                excluded.setdefault(d["id"], set()).add(free[k].gid)
    out: list[list[Item]] = [[] for _ in groups]
    pool = []
    for k, it in enumerate(free):
        (out[owner[k]] if owner[k] is not None else pool).append(it)
    return out, pool, excluded


# ---------------------------------------------------------------- plan

def plan(conn: sqlite3.Connection, *, gap_h: float = 8, split_km: float = 50, merge_h: float = 36,
         region_km: float = 300, frm: str | None = None, to: str | None = None,
         now: datetime | None = None) -> dict:
    """A dry-run proposal of trips and shoots in the plan-file format. Pure: reads the catalog, writes nothing."""
    now = now or datetime.now(UTC)
    default_offset = repo.get_setting(conn, "default_offset", "+00:00")
    decisions = _decisions(conn)
    rejected = {clean_name(str(d.get("value", ""))).lower() for d in decisions if d["kind"] == "reject_name"}
    stop = _stoplist(conn, [d.get("value", "") for d in decisions if d["kind"] == "reject_name"])
    phone = Phone(conn)
    items, unassigned = load_items(conn, stop, phone)
    if frm:
        items = [i for i in items if str(i.cap)[:10] >= frm]
    if to:
        items = [i for i in items if str(i.cap)[:10] <= to]
    total_frames = len(items)

    locked_items = [i for i in items if i.locked]
    free = [i for i in items if not i.locked]
    segs = _replay_structure(free, segments(free, gap_h, split_km), decisions)
    base_pids = [_sha("s:", free[a].gid, free[b].gid) for a, b in segs]
    groups = [free[a:b + 1] for a, b in segs]
    pool: list[Item] = []
    excluded: dict[str, set[int]] = {}
    if any(d["kind"] in ("include", "exclude") for d in decisions):
        groups, pool, excluded = _replay_members(free, groups, base_pids, decisions)
    shoots = []
    for g, pid in zip(groups, base_pids):
        if g:
            s = _build_shoot(g, phone, default_offset, stop, rejected)
            s["proposal_id"] = pid  # stable under include and exclude
            shoots.append(s)
    cfg = shoot_scoring.load_config(conn)
    unassigned_sessions = shoot_scoring.annotate(shoots, pool, cfg, phone, split_km, haversine_km, excluded)

    # one name per date: suffix duplicates with the part of the day, then a number
    def dupes() -> Counter:
        return Counter((s["shoot_date"], s["name"]["value"].lower()) for s in shoots)

    for s in shoots:
        if dupes()[(s["shoot_date"], s["name"]["value"].lower())] > 1:
            s["name"]["value"] += f" · {_part_of_day(s['_w0'].hour)}"
    count: Counter = Counter()
    for s in shoots:
        key = (s["shoot_date"], s["name"]["value"].lower())
        count[key] += 1
        if dupes()[key] > 1 and count[key] > 1:
            s["name"]["value"] += f" {count[key]}"

    shoot_density.annotate(shoots, phone, cfg, haversine_km)
    locked_shoots = _locked_shoots(conn, locked_items)
    for s in locked_shoots:  # applied shoots keep suggestions too, for any place or subject the user has not set
        s["details"] = shoot_scoring.build_details(s, phone, cfg)
    shoots += locked_shoots
    _add_volumes(conn, shoots)
    shoots.sort(key=lambda s: (s["_t0"], s["proposal_id"]))

    unlocked = [s for s in shoots if not s["locked"]]
    trip_groups = _make_trips(unlocked, phone, merge_h, region_km)
    trips = [_trip_dict(g) for g in trip_groups]
    by_id = {s["proposal_id"]: s for s in shoots}
    for t in trips:
        for sid in t["shoot_ids"]:
            by_id[sid]["trip_id"] = t["proposal_id"]
    trip_by_id = {t["proposal_id"]: t for t in trips}

    # decisions on top
    for d in decisions:
        s, t = by_id.get(d["id"]), trip_by_id.get(d["id"])
        kind = d["kind"]
        if kind == "move" and s is not None and not s["locked"]:
            target = d.get("trip_id")
            if target is not None and target not in trip_by_id:
                continue
            if s["trip_id"]:
                trip_by_id[s["trip_id"]]["shoot_ids"].remove(s["proposal_id"])
            s["trip_id"] = target
            if target:
                trip_by_id[target]["shoot_ids"].append(s["proposal_id"])
        elif kind == "rename" and str(d.get("value", "")).strip():
            value = clean_name(str(d["value"]))
            tgt = s or t
            if tgt is not None and not tgt.get("locked"):
                old = tgt["name"]
                alts = [{"value": old["value"], "source": old["source"]}, *old["alternatives"]]
                tgt["name"] = {"value": value, "source": d.get("source") or "user",
                               "alternatives": [a for a in alts if a["value"] != value]}
                if s is not None and "weak_name" in s["flags"]:
                    s["flags"].remove("weak_name")
        elif kind == "detail" and s is not None and not s["locked"]:
            shoot_scoring.set_detail(s["details"], d.get("field"), d.get("value"), d.get("source") or "user")
        elif kind == "accept" and (s is not None and not s["locked"]):
            s["state"] = "accepted"
            if d.get("confirmed"):
                s["confirmed"] = True  # sh-10: accepted through a stack; Apply sets shoots.confirmed_at
        elif kind == "skip" and (s is not None and not s["locked"]):
            s["state"] = "skipped"
    trips += _locked_trips(conn, [s for s in shoots if s["locked"]])
    for t in trips:
        members = sorted((by_id[i] for i in t["shoot_ids"]), key=lambda s: s["_t0"])
        t["shoot_ids"] = [m["proposal_id"] for m in members]
        if members:
            t["start_date"] = members[0]["shoot_date"]
            t["end_date"] = max(m["shoot_date"] for m in members)
    trips = sorted((t for t in trips if t["shoot_ids"]), key=lambda t: (t["start_date"], t["proposal_id"]))

    pcfg = shoot_places.load_config(conn, cfg)
    places = shoot_places.build(shoots, phone, pcfg, haversine_km, shoot_scoring._off_seconds)
    shoot_density.place_density(places["spots"], {s["proposal_id"]: s for s in shoots})
    for s in shoots:
        for k in [k for k in s if k.startswith("_")]:
            del s[k]
    flagged = sum(1 for s in shoots if s["flags"])
    plan_id = "shoots-" + now.strftime("%Y%m%d-%H%M%S")
    return {
        "plan_id": plan_id, "created_at": now.isoformat(timespec="seconds"),
        # the newest decision already folded in (sh-2): review replays only the ones after it
        "decisions_through": max((d["decision_id"] for d in decisions), default=0),
        "params": {"gap_h": gap_h, "split_km": split_km, "merge_h": merge_h, "region_km": region_km,
                   "from": frm, "to": to,
                   # sh-9: the band cut-offs and beta mode the review draws with (settings, D-127, D-128)
                   "pretick_min": cfg.pretick, "show_min": cfg.show, "beta": cfg.beta},
        "summary": {"frames": total_frames, "shoots": len(shoots), "trips": len(trips), "unassigned": unassigned,
                    "locked": sum(1 for s in shoots if s["locked"]), "flagged": flagged},
        "trips": trips, "shoots": shoots, "unassigned_sessions": unassigned_sessions, "places": places,
    }


def _add_volumes(conn: sqlite3.Connection, shoots: list[dict]) -> None:
    """``volumes``: the drives holding each shoot's frames (primary files, indexed or verified), most frames first."""
    held: dict[int, set[str]] = {}
    for r in conn.execute(
            "SELECT DISTINCT a.group_id AS g, i.volume_id AS v FROM assets a JOIN instances i ON i.asset_id = a.asset_id "
            "WHERE a.group_role = 'primary' AND a.group_id IS NOT NULL AND i.state IN ('indexed', 'verified')"):
        held.setdefault(r["g"], set()).add(r["v"])
    labels = {r["volume_id"]: r["label"] for r in conn.execute("SELECT volume_id, label FROM volumes")}
    for s in shoots:
        c: Counter = Counter(v for g in s["group_ids"] for v in held.get(g, ()))
        s["volumes"] = [{"volume_id": v, "label": labels.get(v, v), "frames": n} for v, n in c.most_common()]


KEEP_PLANS = 10


def write_plan(conn: sqlite3.Connection, home: Path, **kw: Any) -> Path:
    """Run `plan` and write it to ``<home>/plans/<plan_id>.json``. The only writer here."""
    result = plan(conn, **kw)
    folder = Path(home) / "plans"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{result['plan_id']}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    # automatic re-plans after edits (D-147) would pile up: keep the newest few
    for old in sorted(folder.glob("shoots-*.json"))[:-KEEP_PLANS]:
        try:
            old.unlink()
        except OSError:
            pass
    return path


# ---------------------------------------------------------------- calibration

def calibrate(conn: sqlite3.Connection, gap_values=(4, 6, 8, 12), split_km: float = 50) -> dict:
    """Score the boundary step against the frames under date-prefixed human folders (the ground truth)."""
    phone = Phone(conn)
    stop = _stoplist(conn)
    items, _ = load_items(conn, stop, phone)
    items = [i for i in items if not i.locked]
    labels = [i.dated[0] if i.dated else None for i in items]
    n = len(items)
    truth = {k for k in range(1, n) if labels[k - 1] != labels[k] and (labels[k - 1] or labels[k])}
    folders = {lab for lab in labels if lab}
    # a folder's edges: just before its first frame and just after its last (the interleaving of folders with
    # other frames inside its extent is not an edge, so recall is not diluted by it)
    first_at: dict[str, int] = {}
    last_at: dict[str, int] = {}
    for k, lab in enumerate(labels):
        if lab:
            first_at.setdefault(lab, k)
            last_at[lab] = k
    edges = {e for lab in folders for e in (first_at[lab], last_at[lab] + 1) if 0 < e < n}
    results = []
    for gap in gap_values:
        segs = segments(items, gap, split_km)
        starts = [a for a, _ in segs[1:]]
        evaluable = [k for k in starts if labels[k - 1] or labels[k] or (k - 2 >= 0 and labels[k - 2])
                     or (k + 1 < n and labels[k + 1])]
        correct = [k for k in evaluable if any(j in truth for j in (k - 1, k, k + 1))]
        start_set = set(starts)
        hit = [t for t in edges if any(j in start_set for j in (t - 1, t, t + 1))]
        raw_hit = [t for t in truth if any(j in start_set for j in (t - 1, t, t + 1))]
        seg_of = [0] * n
        for si, (a, b) in enumerate(segs):
            for k in range(a, b + 1):
                seg_of[k] = si
        folder_segs: dict[str, set[int]] = {}
        seg_folders: dict[int, set[str]] = {}
        for k, lab in enumerate(labels):
            if lab:
                folder_segs.setdefault(lab, set()).add(seg_of[k])
                seg_folders.setdefault(seg_of[k], set()).add(lab)
        whole = split = merged = 0
        for lab, ss in folder_segs.items():
            if len(ss) > 1:
                split += 1
            else:
                whole += 1
            if any(len(seg_folders[s]) > 1 for s in ss):
                merged += 1
        results.append({
            "gap_h": gap, "shoots": len(segs), "boundaries": len(starts), "evaluable": len(evaluable),
            "correct": len(correct), "precision": round(len(correct) / len(evaluable), 3) if evaluable else None,
            "truth_boundaries": len(edges), "recalled": len(hit),
            "recall": round(len(hit) / len(edges), 3) if edges else None,
            "label_changes": len(truth), "label_changes_hit": len(raw_hit),
            "folders": len(folders), "folders_whole": whole, "folders_split": split, "folders_merged": merged,
        })
    return {"frames": n, "ground_truth_folders": len(folders), "split_km": split_km, "results": results}


# ---------------------------------------------------------------- apply and undo (sh-4)

class ApplyError(ValueError):
    """Apply or undo refused, with a plain message (nothing was written)."""


def _unique_name(conn: sqlite3.Connection, shoot_date: str, name: str) -> str:
    """``name``, or ``name 2``, ``name 3``… when (shoot_date, name) is taken (the shoots UNIQUE key)."""
    taken = {r["name"].lower() for r in conn.execute("SELECT name FROM shoots WHERE shoot_date = ?", (shoot_date,))}
    if name.lower() not in taken:
        return name
    n = 2
    while f"{name} {n}".lower() in taken:
        n += 1
    return f"{name} {n}"


def apply(conn: sqlite3.Connection, *, params: dict | None = None, expect: int | None = None,
          now: datetime | None = None) -> dict:
    """Create the accepted shoots and their trips in the catalog, and point their frames at them (sh-4).

    The plan is computed again from the catalog and every stored decision, with the reviewed plan's ``params``; only
    shoots whose state is ``accepted`` and that are not locked are created. ``expect`` is the number of accepted shoots
    the review showed: when the catalog now gives a different number (new frames, a decision made elsewhere), nothing
    is written and the review should re-plan. Each frame's previous ``shoot_id`` goes to ``shoot_apply_log`` under an
    ``apply-<UTC time>`` id, which `undo` takes. ``local_offset`` is stored only when the plan proposed a time-zone
    change, since a shoot's offset re-times its frames' wall clock. The caller holds the transaction. Files are never
    touched."""
    now = now or datetime.now(UTC)
    p = dict(params or {})
    kw = {k: p[k] for k in ("gap_h", "split_km", "merge_h", "region_km") if p.get(k) is not None}
    kw.update({"frm": p.get("from"), "to": p.get("to")})
    result = plan(conn, now=now, **kw)
    accepted = [s for s in result["shoots"] if s.get("state") == "accepted" and not s["locked"]]
    if expect is not None and expect != len(accepted):
        raise ApplyError(f"the review showed {expect} accepted shoots, but the catalog now gives {len(accepted)}: "
                         "re-plan and check again")
    if not accepted:
        raise ApplyError("no accepted shoots to apply")
    base = "apply-" + now.strftime("%Y%m%d-%H%M%S")
    apply_id, n = base, 1
    while conn.execute("SELECT 1 FROM shoot_apply_log WHERE job_id = ? LIMIT 1", (apply_id,)).fetchone():
        n += 1
        apply_id = f"{base}-{n}"
    at = now.isoformat(timespec="seconds")
    trips = {t["proposal_id"]: t for t in result["trips"]}
    trip_ids: dict[str, int] = {}
    for tid in dict.fromkeys(s["trip_id"] for s in accepted if s["trip_id"]):
        t = trips[tid]
        dates = sorted(s["shoot_date"] for s in accepted if s["trip_id"] == tid)
        trip_ids[tid] = conn.execute(
            "INSERT INTO trips (name, start_date, end_date, created_at) VALUES (?,?,?,?)",
            (t["name"]["value"], dates[0], dates[-1], at)).lastrowid
    frames = renamed = 0
    created = []
    for s in accepted:
        name = _unique_name(conn, s["shoot_date"], s["name"]["value"])
        renamed += name != s["name"]["value"]
        off = s["local_offset"]["value"] if s["local_offset"].get("changes") else None
        sid = conn.execute(
            "INSERT INTO shoots (name, shoot_date, kind, created_at, trip_id, local_offset, name_source) "
            "VALUES (?,?,'general',?,?,?,?)",
            (name, s["shoot_date"], at, trip_ids.get(s["trip_id"]), off, s["name"]["source"])).lastrowid
        _write_details(conn, sid, s, at)
        created.append({"shoot_id": sid, "name": name, "shoot_date": s["shoot_date"], "proposal_id": s["proposal_id"]})
        for gid in s["group_ids"]:
            cur = conn.execute("UPDATE capture_groups SET shoot_id = ? WHERE group_id = ? AND shoot_id IS NULL",
                               (sid, gid))
            if cur.rowcount:
                conn.execute("INSERT INTO shoot_apply_log (job_id, group_id, prev_shoot_id) VALUES (?,?,NULL)",
                             (apply_id, gid))
                frames += 1
    return {"apply_id": apply_id, "shoots": len(created), "trips": len(trip_ids), "frames": frames,
            "renamed": renamed, "created": created}


def _write_details(conn: sqlite3.Connection, shoot_id: int, s: dict, at: str | None = None) -> None:
    """Store the details the user set (decision `detail`, source ``user``) in ``shoots.place`` / ``subject`` /
    ``subject_source``. Suggestions from the phone or folders are not written unless the shoot was confirmed through a
    stack (then the proposed value counts as confirmed, with its source kept as phone / folder / model). ``confirmed_at``
    is set (to ``at``) for shoots confirmed through a stack (an `accept` decision with ``confirmed``)."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(shoots)")}
    det = s.get("details") or {}
    sets, vals = [], []
    ok = lambda f: (det.get(f) or {}).get("value") and ((det[f].get("source") == "user") or s.get("confirmed"))  # noqa: E731
    if "place" in cols and ok("place"):
        sets.append("place = ?")
        vals.append(det["place"]["value"])
    if {"subject", "subject_source"} <= cols and ok("subject"):
        src = str(det["subject"].get("source") or "user")
        sets += ["subject = ?", "subject_source = ?"]
        vals += [det["subject"]["value"], "phone" if src.startswith("phone") else src if src in ("folder", "model") else "user"]
    if "confirmed_at" in cols and s.get("confirmed"):
        sets.append("confirmed_at = ?")
        vals.append(at or datetime.now(UTC).isoformat(timespec="seconds"))
    if sets:
        conn.execute(f"UPDATE shoots SET {', '.join(sets)} WHERE shoot_id = ?", (*vals, shoot_id))


def list_applies(conn: sqlite3.Connection) -> list[dict]:
    """Applies that can still be undone, newest first: ``{apply_id, frames, shoots}``."""
    rows = conn.execute(
        "SELECT l.job_id AS apply_id, COUNT(*) AS frames, COUNT(DISTINCT g.shoot_id) AS shoots "
        "FROM shoot_apply_log l LEFT JOIN capture_groups g ON g.group_id = l.group_id "
        "GROUP BY l.job_id ORDER BY l.job_id DESC").fetchall()
    return [dict(r) for r in rows]


def undo(conn: sqlite3.Connection, apply_id: str) -> dict:
    """Undo one apply (sh-4): each logged frame gets its previous ``shoot_id`` back, then the shoots and trips it
    created are deleted once nothing refers to them. The caller holds the transaction."""
    rows = conn.execute("SELECT l.group_id, l.prev_shoot_id, g.shoot_id AS now_shoot FROM shoot_apply_log l "
                        "LEFT JOIN capture_groups g ON g.group_id = l.group_id WHERE l.job_id = ?",
                        (apply_id,)).fetchall()
    if not rows:
        raise ApplyError(f"no apply {apply_id!r} to undo")
    made = {r["now_shoot"] for r in rows if r["now_shoot"] is not None}
    for r in rows:
        conn.execute("UPDATE capture_groups SET shoot_id = ? WHERE group_id = ?", (r["prev_shoot_id"], r["group_id"]))
    conn.execute("DELETE FROM shoot_apply_log WHERE job_id = ?", (apply_id,))
    shoots_gone = 0
    trips = set()
    for sid in sorted(made):
        used = conn.execute("SELECT (SELECT COUNT(*) FROM capture_groups WHERE shoot_id = ?) + "
                            "(SELECT COUNT(*) FROM sequences WHERE shoot_id = ?)", (sid, sid)).fetchone()[0]
        if used:
            continue
        t = conn.execute("SELECT trip_id FROM shoots WHERE shoot_id = ?", (sid,)).fetchone()
        if t is not None and t["trip_id"] is not None:
            trips.add(t["trip_id"])
        shoots_gone += conn.execute("DELETE FROM shoots WHERE shoot_id = ?", (sid,)).rowcount
    trips_gone = 0
    for tid in sorted(trips):
        if not conn.execute("SELECT 1 FROM shoots WHERE trip_id = ? LIMIT 1", (tid,)).fetchone():
            trips_gone += conn.execute("DELETE FROM trips WHERE trip_id = ?", (tid,)).rowcount
    return {"apply_id": apply_id, "frames": len(rows), "shoots": shoots_gone, "trips": trips_gone}



def calibrate_members(conn: sqlite3.Connection, **plan_kw: Any) -> dict:
    """Aggregate numbers for sh-8: candidates per band, how the dated-folder ground truth agrees with them, and how
    many shoots get a tier-0 subject. Read-only; counts only, no names."""
    p = plan(conn, **plan_kw)
    stop = _stoplist(conn)
    items, _ = load_items(conn, stop, Phone(conn))
    folder_of = {i.gid: (i.dated[0] if i.dated else None) for i in items}
    pos = {i.gid: k for k, i in enumerate(items)}
    bands: Counter = Counter()
    truth: dict[str, Counter] = {b: Counter() for b in ("likely", "maybe", "beyond_cutoff")}
    for s in p["shoots"]:
        if s["locked"]:
            continue
        own = Counter(folder_of.get(g) for g in s["group_ids"] if folder_of.get(g))
        home_folder = own.most_common(1)[0][0] if own else None
        for c in s["candidates"]:
            bands[c["band"]] += 1
            a, b = sorted((pos[c["session"][0]], pos[c["session"][1]]))
            f = Counter(folder_of.get(i.gid) for i in items[a:b + 1] if folder_of.get(i.gid))
            sf = f.most_common(1)[0][0] if f else None
            kind = ("unlabelled" if home_folder is None or sf is None
                    else "same_folder" if sf == home_folder else "other_folder")
            truth[c["band"]][kind] += 1
    sh = [s for s in p["shoots"] if not s["locked"]]
    conf = [s["details"]["subject"]["confidence"] for s in sh if s["details"]["subject"]["value"]]
    buckets = Counter("<0.3" if c < 0.3 else "0.3-0.6" if c < 0.6 else ">=0.6" for c in conf)
    src = lambda s, f: str(s["details"][f]["source"])  # noqa: E731
    return {
        "shoots": len(sh), "candidates": sum(bands.values()), "bands": dict(bands),
        "folder_agreement": {b: dict(v) for b, v in truth.items()},
        "subject": {"with_value": len(conf), "by_confidence": dict(buckets),
                    "phone_sourced": sum(1 for s in sh if src(s, "subject").startswith("phone")),
                    "folder_sourced": sum(1 for s in sh if src(s, "subject") == "folder"),
                    "only_beyond_cutoff": sum(1 for s in sh if s["details"]["subject"]["value"] is None
                                              and s["details"]["subject"]["alternatives"]),
                    "with_example_caption": sum(1 for s in sh if s["details"]["subject"]["example_caption"])},
        "place": {"with_value": sum(1 for s in sh if s["details"]["place"]["value"]),
                  "spot": sum(1 for s in sh if src(s, "place") in ("phone_place", "phone_venue")),
                  "area_only": sum(1 for s in sh if src(s, "place") == "phone_area")},
        "people": {"shoots_with_people": sum(1 for s in sh if s["details"]["people"]["value"])},
    }
