"""Place collections (pl-1, D-138) on synthetic shoots. Names and coordinates are invented."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from photovault import shoot_places as sp
from photovault.shoots import haversine_km

CFG = sp.PlaceConfig()


def off(text, cache):
    return 0


def shoot(pid, date, lat=None, lon=None, place=None, source="phone_place", conf=0.8, alts=(), humans=(), locked=False,
          frames=10):
    s = {"proposal_id": pid, "shoot_date": date, "start_instant": date + "T10:00:00+00:00", "locked": locked,
         "name": {"value": "x", "source": "fallback"}, "counts": {"frames": frames},
         "centroid": {"lat": lat, "lon": lon} if lat is not None else None, "_humans": set(humans)}
    if not locked:
        s["details"] = {"place": {"value": place, "source": source if place else None, "confidence": conf if place else 0.0,
                                  "alternatives": [{"value": v, "source": src} for v, src in alts]}}
    return s


def build(shoots, pts=()):
    return sp.build(shoots, SimpleNamespace(pts=list(pts)), CFG, haversine_km, off)


def test_spot_of_three_visits_over_two_years():
    shoots = [shoot("a", "2022-05-01", 10.0, 20.0, "Pier One", "phone_venue"),
              shoot("b", "2023-06-01", 10.001, 20.001, "Pier One", "phone_venue"),
              shoot("c", "2024-07-01", 10.002, 20.0, "Pier Point", "phone_place", alts=[("Bayton", "phone_area")]),
              shoot("lone", "2024-08-01", 40.0, 50.0, "Elsewhere")]
    out = build(shoots)
    assert len(out["spots"]) == 1  # the lone shoot is below place_min_shoots
    spot = out["spots"][0]
    assert [m["proposal_id"] for m in spot["members"]] == ["a", "b", "c"]
    assert spot["name"]["value"] == "Pier One" and spot["name"]["source"] == "phone_venue" and spot["name"]["count"] == 2
    assert {a["value"] for a in spot["name"]["alternatives"]} == {"Pier Point", "Bayton"}
    assert (spot["first_date"], spot["last_date"], spot["frames"]) == ("2022-05-01", "2024-07-01", 30)
    assert spot["key"].startswith("p:") and all(m["via"] == "coords" for m in spot["members"])
    assert out["areas"][0]["spot_keys"] == [spot["key"]]


def test_key_is_stable_and_order_free():
    a = [shoot("a", "2022-05-01", 10.0, 20.0, "P"), shoot("b", "2022-06-01", 10.0, 20.0, "P")]
    assert build(a)["spots"][0]["key"] == build(list(reversed(a)))["spots"][0]["key"]


def test_coastline_chain_is_cut_at_five_km():
    # a chain of shoots 1.2 km apart along a meridian: 8 points span about 8.4 km
    step = 1.2 / 111.19
    shoots = [shoot(f"s{i}", f"2023-0{i + 1}-01", 10 + i * step, 20.0, "Coast") for i in range(8)]
    out = build(shoots)
    assert len(out["spots"]) >= 2
    for spot in out["spots"]:
        lats = [next(s for s in shoots if s["proposal_id"] == m["proposal_id"])["centroid"]["lat"] for m in spot["members"]]
        assert (max(lats) - min(lats)) * 111.19 <= 5.0 + 1e-6
    assert len(out["areas"]) == 1  # still one area


def test_confirmed_name_joins_a_shoot_without_gps():
    shoots = [shoot("a", "2022-05-01", 10.0, 20.0, "Mill Pond", "user", 1.0),
              shoot("b", "2022-06-01", 10.0, 20.0, "Mill Pond", "user", 1.0),
              shoot("n", "2023-01-01", None, None, "Mill Pond", "user", 1.0),
              shoot("m", "2023-02-01", None, None, "Mill Pond", "phone_place", 0.9)]
    spot = build(shoots)["spots"][0]
    via = {m["proposal_id"]: m["via"] for m in spot["members"]}
    assert via == {"a": "coords", "b": "coords", "n": "name"}  # an unconfirmed name joins nothing
    assert spot["name"]["source"] == "user"


def test_shared_human_folder_joins():
    shoots = [shoot("a", "2022-05-01", 10.0, 20.0, "P", humans={"Hill Walks"}),
              shoot("b", "2022-06-01", 10.0, 20.0, "P"),
              shoot("n", "2023-01-01", humans={"Hill Walks"}), shoot("z", "2023-01-02", humans={"Other"})]
    via = {m["proposal_id"]: m["via"] for m in build(shoots)["spots"][0]["members"]}
    assert via["n"] == "folder" and "z" not in via


def test_locked_shoots_are_members():
    shoots = [shoot("a", "2022-05-01", 10.0, 20.0, "P"), shoot("L", "2021-05-01", 10.0, 20.0, locked=True)]
    ids = {m["proposal_id"] for m in build(shoots)["spots"][0]["members"]}
    assert ids == {"a", "L"}


def test_home_is_the_night_spot():
    shoots = [shoot("a", "2022-05-01", 10.0, 20.0, "Flat"), shoot("b", "2022-06-01", 10.0, 20.0, "Flat"),
              shoot("c", "2022-07-01", 12.0, 22.0, "Park"), shoot("d", "2022-08-01", 12.0, 22.0, "Park")]
    night = datetime(2022, 5, 2, 23, 30, tzinfo=UTC).timestamp()
    noon = datetime(2022, 5, 2, 12, 0, tzinfo=UTC).timestamp()
    pts = [(night + i, 10.0, 20.0, None, None, None, None, None) for i in range(12)]
    pts += [(noon + i, 12.0, 22.0, None, None, None, None, None) for i in range(30)]  # daytime: not counted
    homes = {s["name"]["value"]: s["home"] for s in build(shoots, pts)["spots"]}
    assert homes == {"Flat": True, "Park": False}


def test_faint_members_are_beyond_cutoff_in_beta_only():
    shoots = [shoot("a", "2022-05-01", 10.0, 20.0, "P"), shoot("b", "2022-06-01", 10.0, 20.0, "P", conf=0.05)]
    m = build(shoots)["spots"][0]["members"]
    assert m[1].get("beyond_cutoff") is True and "beyond_cutoff" not in m[0]
    cfg = sp.PlaceConfig(beta=False, min_shoots=1)
    out = sp.build(shoots, SimpleNamespace(pts=[]), cfg, haversine_km, off)
    assert [x["proposal_id"] for x in out["spots"][0]["members"]] == ["a"]
