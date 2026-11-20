"""Membership confidence and details for the shoot planner (sh-8, D-127, D-128).

Pure functions over the planner's items: nothing here touches the catalog except `load_config`. `annotate` adds
``members``, ``candidates`` and ``details`` to every unlocked shoot dict. Candidates are scored per *session*
(frames of any body chained by gaps of at most 15 minutes), never per frame, so the work stays near-linear.

Beta mode (``shoot_review_beta``, default true): candidates below ``shoot_show_min``, phone evidence beyond the
decay table's "dropped beyond" limits and detail alternatives a full run would discard stay in the plan with
``"beyond_cutoff": true`` and their computed confidence. With beta off they are omitted.
"""

from __future__ import annotations

import json
import math
import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from .catalog import repo
from .timemodel import offset_tz

SESSION_GAP_S = 900
# One place for everything that can be calibrated (sh-8). Each score term is 0..1; the weights below combine them
# (terms that are unknown, such as place for an unlocated session, drop out and the rest are renormalised).
WEIGHTS = {"time": 0.50, "place": 0.15, "continuity": 0.25, "clock": 0.10}
CONTINUITY = {"sequence": 1.0, "folder": 1.0, "human_folder": 0.8, "same_body": 0.6, "other_body": 0.3,
              "different_folder": 0.0}
CLOCK = {"gps": 1.0, "aligned": 1.0, "exif_offset": 0.8, "assumed_offset": 0.5, "assumed_other_body": 0.2}
TIME_SCALE_MIN_S = 2 * 3600      # time term = 1 / (1 + (gap / scale)^2), scale = max(largest internal gap, this)
WINDOW_MIN_S = 6 * 3600          # candidates lie within max(3 x largest internal gap, this) of the core
BETA_WINDOW_MIN_S = 24 * 3600    # beta (D-128) looks wider, so faint neighbours show faded instead of not at all
# attribute: (full weight within s, half-life s, dropped beyond s) of the time distance to the core span
DECAY = {"area": (3600, 10800, 43200), "spot": (1800, 3600, 10800), "subject": (900, 1800, 7200)}
SPOT_MAX_S = 3600                # beyond this a location hints at the area only, never a spot
REACH_S = 24 * 3600              # phone points looked at around the core (beta shows the ones beyond the limits)
STRENGTH_K = 1.6                 # strength of weighted evidence W: 1 - exp(-K W)
MAX_CANDIDATES = 40
META_PLACE_WEIGHT = 5.0           # a place saved on the shoot's photos counts as this many in-range phone points
# sh-11 split suggestions: terms 0..1 combined like WEIGHTS (an unknown term drops out); the gap term is always known.
SPLIT_WEIGHTS = {"place": 0.40, "subject": 0.25, "gap": 0.35}
SPLIT_MIN_GAP_S = 20 * 60        # setting shoot_split_min_gap_s
SPLIT_WINDOW_S = 3600            # phone and camera evidence on each side of a gap
SPLIT_MOVE_KM = (1.0, 5.0)       # place term: 0 at the first distance, 1 at the second
MAX_SPLIT_SUGGESTIONS = 5
MAX_ALTERNATIVES = 8
FOLDER_DATED_CONF, FOLDER_HUMAN_CONF = 0.9, 0.5
DEFAULT_LABEL_STOPLIST = ["sky", "outdoor", "outdoors", "indoor", "plant", "tree", "grass", "building", "cloud",
                          "clouds", "people", "person", "nature", "text", "sunlight", "day",
                          # generic scene and object labels that name no subject (user report, 2026-11-19)
                          "cloudy", "sunny", "activity", "land", "apartment", "room", "interior room", "furniture",
                          "bottle", "container", "drink", "water", "wood", "rock", "structure", "vehicle", "machine",
                          "adult", "child", "hand", "clothing", "footwear", "document", "art", "material", "liquid"]

# Folder and album names that describe storage, not a shoot (D-146). Used for shoot names (via `shoots`), folder
# subjects and phone albums.
GENERIC_NAME_RES = [re.compile(p, re.I) for p in (
    r"^photography$", r"^organi[sz]ed( raw)?( photos)?$", r"^(my )?photos?( library)?$", r"^archived?( raw)?( photos)?$", r"^raw photos$",
    r"^\d+(st|nd|rd|th)\s+card$", r"^(first|second|third)\s+card$", r".*\b\d+(\.\d+)?\s*[gt]b\b.*",
    r"^(jpe?g|nef|arw|cr[23]|dng|heic)s?$", r"^lightroom\b.*", r"^(un)?sorted$", r"^to (sort|edit)$",
    r"^downloads?$", r"^desktop$", r"^recents?$", r"^favou?rites$", r"^camera roll$", r"^instagram$",
)]


def is_generic_name(name: str) -> bool:
    n = name.strip()
    return any(r.match(n) for r in GENERIC_NAME_RES)


def short_place(name: str) -> str:
    """The first part of a phone place name ("Hull, Massachusetts, United States" -> "Hull"), so titles stay short."""
    parts = [x.strip() for x in name.split(",") if x.strip()]
    return parts[0] if parts else name.strip()
_FALSE = ("0", "false", "no", "off")


@dataclass
class Config:
    pretick: float = 0.5
    show: float = 0.1
    beta: bool = True
    stoplist: set[str] = field(default_factory=lambda: set(DEFAULT_LABEL_STOPLIST))
    weights: dict = field(default_factory=lambda: dict(WEIGHTS))
    decay: dict = field(default_factory=lambda: dict(DECAY))
    split_min_gap_s: float = SPLIT_MIN_GAP_S
    split_weights: dict = field(default_factory=lambda: dict(SPLIT_WEIGHTS))


def _float(conn, key: str, default: float) -> float:
    try:
        return float(repo.get_setting(conn, key, None))
    except (TypeError, ValueError):
        return default


def _json(conn, key: str) -> Any:
    raw = repo.get_setting(conn, key, None)
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def load_config(conn) -> Config:
    """Settings with defaults: ``shoot_pretick_min``, ``shoot_show_min``, ``shoot_review_beta``,
    ``shoot_label_stoplist`` (JSON list), ``shoot_score_weights`` and ``shoot_phone_decay`` (JSON objects)."""
    cfg = Config(pretick=_float(conn, "shoot_pretick_min", 0.5), show=_float(conn, "shoot_show_min", 0.1))
    cfg.split_min_gap_s = _float(conn, "shoot_split_min_gap_s", SPLIT_MIN_GAP_S)
    sw = _json(conn, "shoot_split_weights")
    if isinstance(sw, dict):
        cfg.split_weights.update({k: float(v) for k, v in sw.items() if k in SPLIT_WEIGHTS and isinstance(v, (int, float))})
    beta = repo.get_setting(conn, "shoot_review_beta", "true")
    cfg.beta = str(beta).strip().lower() not in _FALSE
    stop = _json(conn, "shoot_label_stoplist")
    if isinstance(stop, list):
        cfg.stoplist = {str(x).strip().lower() for x in stop}
    w = _json(conn, "shoot_score_weights")
    if isinstance(w, dict):
        cfg.weights.update({k: float(v) for k, v in w.items() if k in WEIGHTS and isinstance(v, (int, float))})
    dec = _json(conn, "shoot_phone_decay")
    if isinstance(dec, dict):
        for k, v in dec.items():
            if k in DECAY and isinstance(v, list) and len(v) == 3:
                cfg.decay[k] = tuple(float(x) for x in v)
    return cfg


# ---------------------------------------------------------------- sessions

class Session:
    __slots__ = ("items", "first", "last", "t0", "t1", "owner", "home_gap", "body", "seqs", "dated", "humans",
                 "prov", "lat", "lon")


def make_sessions(items: list, owner: int | None) -> list[Session]:
    """Sessions of a time-sorted item list (gaps above 15 minutes break them)."""
    out: list[Session] = []
    cur: list = []

    def close() -> None:
        s = Session()
        s.items, s.owner = cur, owner
        s.first, s.last = cur[0].gid, cur[-1].gid
        s.t0, s.t1 = cur[0].ts, cur[-1].ts
        s.body = Counter(i.body for i in cur)
        s.seqs = {i.seq for i in cur if i.seq is not None}
        s.dated = {i.dated[0] for i in cur if i.dated}
        s.humans = {h for i in cur for h in i.humans}
        s.prov = Counter(getattr(i, "prov", "assumed_offset") for i in cur)
        loc = [(i.lat, i.lon) for i in cur if i.lat is not None]
        s.lat, s.lon = ((sum(p[0] for p in loc) / len(loc), sum(p[1] for p in loc) / len(loc)) if loc else (None, None))
        s.home_gap = math.inf
        out.append(s)

    for it in items:
        if cur and it.ts - cur[-1].ts > SESSION_GAP_S:
            close()
            cur = []
        cur.append(it)
    if cur:
        close()
    for k, s in enumerate(out):  # gap to the nearest other session of the same group
        gaps = []
        if k:
            gaps.append(s.t0 - out[k - 1].t1)
        if k + 1 < len(out):
            gaps.append(out[k + 1].t0 - s.t1)
        s.home_gap = max(0.0, min(gaps)) if gaps and owner is not None else math.inf
    return out


# ---------------------------------------------------------------- text helpers

def _dur(seconds: float) -> str:
    m = max(1, round(seconds / 60))
    if m < 60:
        return f"{m} min"
    h, m = divmod(m, 60)
    if h >= 48:
        return f"{h // 24} d {h % 24} h" if h % 24 else f"{h // 24} d"
    return f"{h} h {m} min" if m else f"{h} h"


# ---------------------------------------------------------------- candidates

def _score(ctx: dict, s: Session, home_ctx: dict | None, cfg: Config, split_km: float,
           dist: Callable[[float, float, float, float], float]) -> tuple[float, list[dict]]:
    c0, c1 = ctx["t0"], ctx["t1"]
    g = max(0.0, s.t0 - c1, c0 - s.t1)
    scale = max(ctx["largest_gap"], TIME_SCALE_MIN_S)
    time_t = 1.0 / (1.0 + (g / scale) ** 2)
    if s.home_gap < g:  # another core is closer: pushed out
        time_t *= min(1.0, (s.home_gap + scale / 4) / (g + scale / 4))
    if g == 0:
        time_text = "overlaps the shoot"
    elif s.t0 > c1:
        time_text = f"+{_dur(g)} after"
    else:
        time_text = f"-{_dur(g)} before"

    place_t, place_text = None, None
    if s.lat is not None and ctx["points"]:
        d = min(dist(s.lat, s.lon, p[0], p[1]) for p in ctx["points"])
        place_t = 1.0 if d <= split_km * 0.1 else max(0.0, 1.0 - d / split_km)
        if home_ctx and home_ctx["points"]:
            dh = min(dist(s.lat, s.lon, p[0], p[1]) for p in home_ctx["points"])
            if dh + 1 < d * 0.5:
                place_t *= 0.5
        place_text = "same place" if d < 0.5 else f"{d:.0f} km away" if d >= 1 else f"{d:.1f} km away"

    cont_t, cont_text = None, None
    if s.seqs & ctx["seqs"]:
        cont_t, cont_text = CONTINUITY["sequence"], "same sequence"
    elif s.dated & ctx["dated"]:
        cont_t, cont_text = CONTINUITY["folder"], "same folder"
    elif s.humans & ctx["humans"]:
        cont_t, cont_text = CONTINUITY["human_folder"], "same folder name"
    elif s.dated and ctx["dated"]:
        cont_t, cont_text = CONTINUITY["different_folder"], "different dated folder"
    else:
        shared = set(s.body) & ctx["bodies"]
        if shared:
            cont_t, cont_text = CONTINUITY["same_body"], "same body"
        else:
            cont_t, cont_text = CONTINUITY["other_body"], f"other body ({', '.join(sorted(s.body))})"

    other = not (set(s.body) & ctx["bodies"])
    prov = s.prov.most_common(1)[0][0]
    if prov == "assumed_offset" and other:
        clock_t, clock_text = CLOCK["assumed_other_body"], "other body, clock assumed"
    else:
        clock_t = CLOCK.get(prov, 0.5)
        clock_text = {"gps": "clock from GPS", "aligned": "clock aligned", "exif_offset": None,
                      "assumed_offset": "clock assumed (no camera offset)"}.get(prov)

    terms = {"time": (time_t, time_text), "place": (place_t, place_text), "continuity": (cont_t, cont_text),
             "clock": (clock_t, clock_text)}
    num = den = 0.0
    for k, (t, _) in terms.items():
        if t is not None:
            num += cfg.weights[k] * t
            den += cfg.weights[k]
    score = num / den if den else 0.0
    ranked = sorted((k for k, (t, text) in terms.items() if t is not None and text),
                    key=lambda k: -cfg.weights[k] * abs(terms[k][0] - 0.5))
    reasons = [{"term": k, "text": terms[k][1]} for k in ranked[:3]]
    if not any(r["term"] == "time" for r in reasons):
        reasons = [{"term": "time", "text": time_text}, *reasons[:2]]
    return score, reasons


def _candidates(ctxs: list[dict], allsess: list[Session], cfg: Config, split_km: float, dist) -> None:
    allsess.sort(key=lambda s: s.t0)
    t0s = [s.t0 for s in allsess]
    maxspan = max((s.t1 - s.t0 for s in allsess), default=0.0)
    by_idx = {c["idx"]: c for c in ctxs}
    for ctx in ctxs:
        window = max(3 * ctx["largest_gap"], BETA_WINDOW_MIN_S if cfg.beta else WINDOW_MIN_S)
        lo = bisect_left(t0s, ctx["t0"] - window - maxspan)
        hi = bisect_right(t0s, ctx["t1"] + window)
        found = []
        for s in allsess[lo:hi]:
            if s.owner == ctx["idx"]:
                continue
            if max(0.0, s.t0 - ctx["t1"], ctx["t0"] - s.t1) > window:
                continue
            if ctx["excluded"] and any(i.gid in ctx["excluded"] for i in s.items):
                continue
            home = by_idx.get(s.owner)
            score, reasons = _score(ctx, s, home, cfg, split_km, dist)
            band = "likely" if score >= cfg.pretick else "maybe" if score >= cfg.show else "beyond_cutoff"
            if band == "beyond_cutoff" and not cfg.beta:
                continue
            cand = {"session": [s.first, s.last], "score": round(score, 3), "band": band, "reasons": reasons,
                    "home_proposal": home["pid"] if home else None, "frames": len(s.items),
                    "start_instant": datetime.fromtimestamp(s.t0, UTC).isoformat(timespec="seconds"),
                    "end_instant": datetime.fromtimestamp(s.t1, UTC).isoformat(timespec="seconds"),
                    "bodies": dict(s.body)}
            if band == "beyond_cutoff":
                cand["beyond_cutoff"] = True
            found.append(cand)
        found.sort(key=lambda c: (-c["score"], c["session"][0]))
        ctx["shoot"]["candidates"] = found[:MAX_CANDIDATES]


# ---------------------------------------------------------------- details

class _Acc:
    def __init__(self) -> None:
        self.entries: dict[tuple[str, str], dict] = {}
        self.tot_in: Counter = Counter()
        self.tot_bey: Counter = Counter()
        self._in: dict[str, float] = {}
        self._bey: dict[str, float] = {}

    def begin(self) -> None:
        self._in, self._bey = {}, {}

    def add(self, kind: str, value: str, source: str, w: float, beyond: bool) -> None:
        e = self.entries.setdefault((kind, value.lower()), {"kind": kind, "value": value, "source": source,
                                                            "w_in": 0.0, "n_in": 0, "w_bey": 0.0, "n_bey": 0})
        if beyond:
            e["w_bey"] += w
            e["n_bey"] += 1
            self._bey[kind] = max(self._bey.get(kind, 0.0), w)
        else:
            e["w_in"] += w
            e["n_in"] += 1
            self._in[kind] = max(self._in.get(kind, 0.0), w)

    def end(self) -> None:
        for k, w in self._in.items():
            self.tot_in[k] += w
        for k, w in self._bey.items():
            self.tot_bey[k] += w

    def resolve(self, beta: bool) -> tuple[list[dict], list[dict]]:
        normal, beyond = [], []
        for e in self.entries.values():
            if e["w_in"] > 0:
                share = min(1.0, e["w_in"] / self.tot_in[e["kind"]])
                conf = (1 - math.exp(-STRENGTH_K * e["w_in"])) * share
                normal.append({"value": e["value"], "source": e["source"], "confidence": round(conf, 3),
                               "evidence": e["n_in"], "_kind": e["kind"]})
            elif beta and e["w_bey"] > 0:
                share = min(1.0, e["w_bey"] / self.tot_bey[e["kind"]])
                conf = (1 - math.exp(-STRENGTH_K * e["w_bey"])) * share
                beyond.append({"value": e["value"], "source": e["source"], "confidence": round(conf, 3),
                               "evidence": e["n_bey"], "beyond_cutoff": True, "_kind": e["kind"]})
        for lst in (normal, beyond):
            lst.sort(key=lambda x: (-x["confidence"], x["value"]))
        return normal, beyond


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if not k.startswith("_")}


def _detail(normal: list[dict], beyond: list[dict]) -> dict:
    if normal:
        top, rest = normal[0], normal[1:]
        out = {"value": top["value"], "source": top["source"], "confidence": top["confidence"],
               "evidence": top["evidence"]}
    else:
        rest = []
        out = {"value": None, "source": None, "confidence": 0.0, "evidence": 0}
    out["alternatives"] = [_clean(a) for a in rest[:MAX_ALTERNATIVES]] + [_clean(a) for a in beyond[:MAX_ALTERNATIVES]]
    return out


def _off_seconds(text: str | None, cache: dict) -> int:
    if not text:
        return 0
    if text not in cache:
        try:
            cache[text] = int(offset_tz(text).utcoffset(None).total_seconds())
        except ValueError:
            cache[text] = 0
    return cache[text]


def _jlist(text: str | None) -> list[str]:
    try:
        v = json.loads(text) if text else []
    except ValueError:
        return []
    return [str(x) for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []


def _jdict(text: str | None) -> dict:
    try:
        v = json.loads(text) if text else {}
    except ValueError:
        return {}
    return v if isinstance(v, dict) else {}


def build_details(s: dict, phone, cfg: Config) -> dict:
    """``{place, subject, people}`` for one shoot, from its frames, folders and the phone points near its core."""
    items = s["_items"]
    t0, t1 = s["_t0"], s["_t1"]
    dates = set()
    d = s["_w0"].date()
    while d <= s["_w1"].date():
        dates.add(d.isoformat())
        d += timedelta(days=1)
    shoot_off = s["local_offset"]["value"]
    offs: dict = {}
    place, subject, people = _Acc(), _Acc(), _Acc()

    # the frames' own metadata (places the user saved on photos): strong evidence, worth several phone points (D-147)
    for kind, attr in (("spot", "place"), ("area", "city")):
        c = Counter(getattr(i, attr) for i in items if getattr(i, attr))
        if c:
            place.begin()
            for v, n in c.items():
                place.add(kind, v, "meta", META_PLACE_WEIGHT * n / sum(c.values()), False)
                place.entries[(kind, v.lower())]["n_in"] += n - 1
            place.end()

    caption = None  # (weight, beyond, text)
    for p in phone.pts[bisect_left(phone.ts, t0 - REACH_S):bisect_right(phone.ts, t1 + REACH_S)]:
        dt = 0.0 if t0 <= p[0] <= t1 else min(abs(p[0] - t0), abs(p[0] - t1))

        def w(kind: str) -> tuple[float, bool]:
            full, half, drop = cfg.decay[kind]
            return (1.0 if dt <= full else 0.5 ** ((dt - full) / half)), dt > drop

        ana = p[8] if len(p) > 8 else None
        caption_t, labels, persons, hints = (ana[0], _jlist(ana[1]), _jlist(ana[2]), _jdict(ana[3])) if ana else (
            None, [], [], {})
        hl = lambda k: [str(x) for x in (hints.get(k) or []) if isinstance(x, str) and x.strip()]  # noqa: E731

        aw, ab = w("area")
        place.begin()
        area = p[5] or p[6]
        if area:
            place.add("area", area, "phone_area", aw, ab)
        sw, sb = w("spot")
        sb = sb or dt > SPOT_MAX_S
        if p[4]:
            place.add("spot", short_place(p[4]), "phone_place", sw, sb)
        for key in ("venues", "landmarks", "areas_of_interest", "bodies_of_water"):
            for v in hl(key):
                place.add("spot", short_place(v), "phone_venue", sw, sb)
        place.end()

        subj_w, subj_b = w("subject")
        subject.begin()
        for v in labels:
            if v.lower() not in cfg.stoplist:
                subject.add("subject", v, "phone_label", subj_w, subj_b)
        for key, src in (("activities", "phone_activity"), ("landmarks", "phone_landmark"),
                         ("events", "phone_event"), ("albums", "phone_album")):
            for v in hl(key):
                if not is_generic_name(v) and v.lower() not in cfg.stoplist:
                    subject.add("subject", v, src, subj_w, subj_b)
        if hl("holidays"):
            local = (datetime.fromtimestamp(p[0], UTC)
                     + timedelta(seconds=_off_seconds(p[3] or shoot_off, offs))).date().isoformat()
            same = local in dates
            for v in hl("holidays"):
                subject.add("subject", v, "phone_holiday", 1.0 if same else 0.0, not same)
        subject.end()

        people.begin()
        for v in persons:
            people.add("people", v, "phone_person", subj_w, subj_b)
        people.end()

        if caption_t and (caption is None or (subj_b, -subj_w) < (caption[1], -caption[0])):
            caption = (subj_w, subj_b, caption_t)

    # place: the best spot if any is in range, else the best area
    normal, beyond = place.resolve(cfg.beta)
    spots = [x for x in normal if x["_kind"] == "spot"]
    areas = [x for x in normal if x["_kind"] == "area"]
    place_d = _detail(spots + areas, beyond)

    # subject: phone terms, then the folders
    s_norm, s_bey = subject.resolve(cfg.beta)
    n = max(1, len(items))
    dated = Counter(i.dated[1] for i in items if i.dated)
    for v, c in dated.most_common(3):
        s_norm.append({"value": v, "source": "folder", "confidence": round(FOLDER_DATED_CONF * c / n, 3),
                       "evidence": c, "_kind": "folder"})
    humans = Counter(h for i in items for h in i.humans[:1])
    for v, c in humans.most_common(3):
        if v.lower() not in {x["value"].lower() for x in s_norm}:
            s_norm.append({"value": v, "source": "folder", "confidence": round(FOLDER_HUMAN_CONF * c / n, 3),
                           "evidence": c, "_kind": "folder"})
    s_norm.sort(key=lambda x: (-x["confidence"], x["value"]))
    subject_d = _detail(s_norm, s_bey)
    subject_d["top_terms"] = [x["value"] for x in s_norm[:3]]
    subject_d["example_caption"] = (
        {"value": caption[2], "confidence": round(caption[0], 3), "beyond_cutoff": caption[1]}
        if caption and (cfg.beta or not caption[1]) else None)

    # people: a list, with one chip per name
    p_norm, p_bey = people.resolve(cfg.beta)
    people_d = {"value": [x["value"] for x in p_norm], "source": "phone_person" if p_norm else None,
                "confidence": p_norm[0]["confidence"] if p_norm else 0.0,
                "evidence": sum(x["evidence"] for x in p_norm),
                "names": [_clean(x) for x in p_norm[:MAX_ALTERNATIVES * 2]],
                "alternatives": [_clean(x) for x in p_bey[:MAX_ALTERNATIVES]]}
    return {"place": place_d, "subject": subject_d, "people": people_d}


def set_detail(details: dict, name: str, value: Any, source: str = "user") -> None:
    """A user decision: the field takes ``value`` with confidence 1; the old value becomes an alternative."""
    cur = details.get(name)
    if cur is None:
        return
    alts = cur.get("alternatives", [])
    if cur.get("value") not in (None, [], value) and name != "people":
        alts = [{"value": cur["value"], "source": cur["source"], "confidence": cur["confidence"],
                 "evidence": cur["evidence"]}, *alts]
    cur["alternatives"] = [a for a in alts if a.get("value") != value]
    cur.update({"value": value, "source": source, "confidence": 1.0})


# ---------------------------------------------------------------- the entry point

def annotate(shoots: list[dict], unassigned: list, cfg: Config, phone, split_km: float,
             dist: Callable[[float, float, float, float], float], excluded: dict[str, set[int]]) -> list[dict]:
    """Add ``members``, ``candidates`` and ``details`` to each unlocked shoot (dicts carry private ``_items``).
    Returns the unassigned sessions (frames excluded from every shoot) for the plan's ``unassigned_sessions``."""
    ctxs: list[dict] = []
    allsess: list[Session] = []
    for idx, s in enumerate(shoots):
        sess = make_sessions(s["_items"], idx)
        allsess += sess
        items = s["_items"]
        gaps = [b.ts - a.ts for a, b in zip(items, items[1:])]
        ctx = {"idx": idx, "shoot": s, "pid": s["proposal_id"], "t0": s["_t0"], "t1": s["_t1"],
               "largest_gap": max(gaps, default=0.0), "points": s["points"],
               "bodies": {i.body for i in items}, "seqs": {i.seq for i in items if i.seq is not None},
               "dated": {i.dated[0] for i in items if i.dated}, "humans": {h for i in items for h in i.humans},
               "excluded": excluded.get(s["proposal_id"], set())}
        ctxs.append(ctx)
        s["members"] = {"core_sessions": [[x.first, x.last] for x in sess]}
    pool = make_sessions(unassigned, None)
    allsess += pool
    _candidates(ctxs, allsess, cfg, split_km, dist)
    for s in shoots:
        s["details"] = build_details(s, phone, cfg)
    return [{"session": [x.first, x.last], "frames": len(x.items),
             "start_instant": datetime.fromtimestamp(x.t0, UTC).isoformat(timespec="seconds"),
             "end_instant": datetime.fromtimestamp(x.t1, UTC).isoformat(timespec="seconds")} for x in pool]
