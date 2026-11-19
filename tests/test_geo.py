"""Geo matching (ui-3): group UTC and geotag suggestions. Pure catalog logic, no exiftool."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from photovault.catalog import db, repo
from photovault.config import load_config
from photovault.geo import group_utc, places_near, session_of, suggest, utc_text


def row(**kw) -> dict:
    base = {"capture_time": None, "capture_offset": None, "time_shift_s": 0}
    base.update(kw)
    return base


def test_group_utc_naive_with_offset() -> None:
    when = group_utc(row(capture_time="2026-11-10T10:00:00", capture_offset="+02:00"), "-05:00")
    assert when == datetime(2026, 11, 10, 8, 0, tzinfo=UTC)


def test_group_utc_naive_without_offset_uses_default() -> None:
    assert group_utc(row(capture_time="2026-11-10T10:00:00"), "-05:00") == datetime(2026, 11, 10, 15, 0, tzinfo=UTC)
    assert group_utc(row(capture_time="2026-11-10T10:00:00"), "+00:00") == datetime(2026, 11, 10, 10, 0, tzinfo=UTC)


def test_group_utc_aware_ignores_offset_and_default() -> None:
    when = group_utc(row(capture_time="2026-11-10T10:00:00+05:30", capture_offset="-08:00"), "-05:00")
    assert when == datetime(2026, 11, 10, 4, 30, tzinfo=UTC)


def test_group_utc_applies_time_shift() -> None:
    assert group_utc(row(capture_time="2026-11-10T10:00:00", capture_offset="+00:00", time_shift_s=-90),
                     "+00:00") == datetime(2026, 11, 10, 9, 58, 30, tzinfo=UTC)
    # a missing shift counts as zero
    assert group_utc({"capture_time": "2026-11-10T10:00:00", "capture_offset": "+00:00"},
                     "+00:00") == datetime(2026, 11, 10, 10, 0, tzinfo=UTC)


def test_group_utc_without_capture_time_is_none() -> None:
    assert group_utc(row(), "+00:00") is None


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    c = db.connect(load_config().catalog_path)
    db.migrate(c)
    return c


T0 = datetime(2026, 11, 10, 10, 0, 0, tzinfo=UTC)


def make_group(conn, shutter: int = 1) -> int:
    body = repo.get_or_create_body(conn, "NIKON Z 6_2", "0000001", "Z6")
    return repo.insert_group(conn, body_id=body, shutter_count=shutter, capture_time="2026-11-10T10:00:00",
                             capture_offset="+00:00", time_source="exif", canonical_stem="DSC_0001",
                             original_stem="DSC_0001", original_num=1, shoot_id=None)


def point(conn, offset_s: float, source: str, ref: str, lat: float = 1.0, lon: float = 2.0,
          alt: float | None = None) -> None:
    from datetime import timedelta

    repo.upsert_geo_point(conn, utc_time=utc_text(T0 + timedelta(seconds=offset_s)), lat=lat, lon=lon, alt=alt,
                          source=source, source_ref=ref)


def test_suggest_returns_place_fields(conn) -> None:
    gid = make_group(conn)
    repo.upsert_geo_point(conn, utc_time=utc_text(T0), lat=12.5, lon=-40.25, alt=None, source="iphone",
                          source_ref="u1", place_name="Harbourton Quay", city="Harbourton", state="Westshire",
                          country="Freedonia", local_offset="+01:00", kind="photo")
    repo.upsert_geo_point(conn, utc_time=utc_text(T0), lat=1.0, lon=2.0, alt=None, source="gpx", source_ref="p1")
    found = {s["source"]: s for s in suggest(conn, gid)}
    assert (found["iphone"]["place_name"], found["iphone"]["city"], found["iphone"]["state"],
            found["iphone"]["country"]) == ("Harbourton Quay", "Harbourton", "Westshire", "Freedonia")
    assert found["gpx"]["place_name"] is None and found["gpx"]["city"] is None


def test_suggest_orders_by_nearness_and_fills_fields(conn) -> None:
    gid = make_group(conn)
    point(conn, -60, "drone", "d1", lat=10.0, lon=20.0, alt=50.0)
    point(conn, 400, "phone", "p1", lat=11.0, lon=21.0)
    point(conn, -1000, "phone", "p0")
    point(conn, 2000, "phone", "far")  # outside the 1800 s window
    out = suggest(conn, gid, limit=10)
    # -60 s and +400 s bracket the frame, so the interpolation comes first; the rest sort by distance
    assert out[0]["source"] == "interpolated"
    assert [s["source_ref"] for s in out[1:]] == ["d1", "p1", "p0"]
    assert out[1] == {"lat": 10.0, "lon": 20.0, "alt": 50.0, "source": "drone", "source_ref": "d1",
                      "delta_s": -60, "confidence": "high", "score": 1.0, "place_name": None, "city": None, "state": None,
                      "country": None}
    assert out[2]["delta_s"] == 400 and out[2]["confidence"] == "medium"
    assert len(suggest(conn, gid)) == 3  # default limit


def test_suggest_excludes_the_groups_own_point(conn) -> None:
    gid = make_group(conn)
    point(conn, 0, "catalog", f"group:{gid}", lat=5.0, lon=5.0)
    point(conn, 30, "phone", "p1")
    assert [s["source_ref"] for s in suggest(conn, gid)] == ["p1"]


def test_suggest_interpolates_between_bracketing_points(conn) -> None:
    gid = make_group(conn)
    point(conn, -300, "drone", "a", lat=10.0, lon=20.0, alt=100.0)
    point(conn, 100, "phone", "b", lat=14.0, lon=26.0, alt=None)
    out = suggest(conn, gid)
    first = out[0]
    assert first["source"] == "interpolated" and first["source_ref"] == "a|b" and first["delta_s"] == 0
    assert first["lat"] == pytest.approx(13.0) and first["lon"] == pytest.approx(24.5)
    assert first["alt"] is None  # one bracket has no altitude
    assert first["confidence"] == "medium"  # the wider gap is 300 s
    assert [s["source_ref"] for s in out[1:]] == ["b", "a"]


def test_suggest_no_interpolation_when_one_side_is_empty(conn) -> None:
    gid = make_group(conn)
    point(conn, 50, "phone", "b")
    point(conn, 80, "phone", "c")
    assert all(s["source"] != "interpolated" for s in suggest(conn, gid))


@pytest.mark.parametrize("offset, bucket", [(120, "high"), (121, "medium"), (900, "medium"), (901, "low")])
def test_confidence_buckets(conn, offset: int, bucket: str) -> None:
    gid = make_group(conn)
    point(conn, offset, "phone", "p")
    [only] = suggest(conn, gid, window_s=1800)
    assert only["confidence"] == bucket


def test_suggest_limit_and_unknown_group(conn) -> None:
    gid = make_group(conn)
    for i in range(5):
        point(conn, 10 * (i + 1), "phone", f"p{i}")
    assert len(suggest(conn, gid, limit=2)) == 2
    with pytest.raises(ValueError):
        suggest(conn, 999)


def test_places_near_distinct_nearest_first(conn) -> None:
    gid = make_group(conn)
    for ref, secs, name, city in [("a", 600, "Harbourton Quay", "Harbourton"), ("b", -120, "Harbourton Quay", "Harbourton"),
                                  ("c", -1800, None, "Northland Falls"), ("d", 9000, "Far Place", "Elsewhere")]:
        repo.upsert_geo_point(conn, utc_time=utc_text(T0 + timedelta(seconds=secs)), lat=1.0, lon=2.0, alt=None,
                              source="iphone", source_ref=ref, place_name=name, city=city, state="Westshire")
    point(conn, 30, "gpx", "g1")  # no place fields: not offered
    out = places_near(conn, gid, window_s=3600)
    assert [(p["place_name"], p["city"]) for p in out] == [("Harbourton Quay", "Harbourton"), (None, "Northland Falls")]
    assert out[0]["delta_s"] == -120 and out[0]["state"] == "Westshire"


# --- sp-8: session, farther tiers, last known place (D-074, D-075); coordinates and places are invented

def frame(conn, n: int, at: datetime) -> int:
    body = repo.get_or_create_body(conn, "NIKON Z 6_2", "0000001", "Z6")
    return repo.insert_group(conn, body_id=body, shutter_count=n + 100,
                             capture_time=at.replace(tzinfo=None).isoformat(timespec="seconds"),
                             capture_offset="+00:00", time_source="exif", canonical_stem=f"DSC_{n:04d}",
                             original_stem=f"DSC_{n:04d}", original_num=n, shoot_id=None)


def phone(conn, at: datetime, ref: str, place: str | None = "Harbourton", lat: float = 10.0, lon: float = 20.0) -> None:
    repo.upsert_geo_point(conn, utc_time=utc_text(at), lat=lat, lon=lon, alt=None, source="iphone", source_ref=ref,
                          place_name=None, city=place, state=None, country=None)


def test_session_of_chains_by_gap(conn) -> None:
    ids = [frame(conn, i, T0 + timedelta(minutes=10 * i)) for i in range(4)]
    frame(conn, 9, T0 + timedelta(hours=3))  # a gap above 15 min
    assert [f["group_id"] for f in session_of(conn, ids[1])] == ids
    assert len(session_of(conn, ids[1], gap_s=300)) == 1


def test_case_a_one_sided_far_is_weak(conn) -> None:
    gid = frame(conn, 1, T0)
    for i in range(2, 30):
        frame(conn, i, T0 + timedelta(seconds=40 * i))
    phone(conn, T0 + timedelta(minutes=106), "p1")  # 1 h 46 min after, nothing before
    out = suggest(conn, gid)
    assert [(s["confidence"], s["source_ref"]) for s in out] == [("weak", "p1")]  # far, lowered one tier
    assert out[0]["delta_s"] == 106 * 60 and out[0]["score"] == 0.2


def test_case_b_day_with_only_hints(conn) -> None:
    gid = frame(conn, 1, T0)
    phone(conn, T0 - timedelta(hours=23), "before", place="Harbourton")
    phone(conn, T0 + timedelta(hours=29), "after", place="Eastvale", lat=11.0)
    out = suggest(conn, gid)
    assert [(s["confidence"], s["source_ref"]) for s in out] == [("hint", "before"), ("hint", "after")]
    assert places_near(conn, gid) == []  # places_near stays on the windowed points


def test_case_c_session_row_and_far_row(conn) -> None:
    gid = frame(conn, 1, T0)
    frame(conn, 2, T0 + timedelta(minutes=10))
    frame(conn, 3, T0 + timedelta(minutes=24))  # chains to the photo with gaps of at most 15 min
    phone(conn, T0 + timedelta(minutes=42), "next", place="Eastvale", lat=30.0)  # 18 min from frame 3
    phone(conn, T0 - timedelta(minutes=50), "prev")
    out = suggest(conn, gid)
    session = [s for s in out if s["source"] == "session"]
    assert [s["source_ref"] for s in session] == ["next"]
    assert session[0]["via"] == {"stem": "DSC_0003", "delta_s": 18 * 60} and session[0]["confidence"] == "low"
    assert session[0]["delta_s"] == 42 * 60
    far = [s for s in out if s["source"] == "iphone"]
    assert {s["source_ref"]: s["confidence"] for s in far} == {"prev": "far", "next": "far"}


def test_case_c_far_tier_for_42_minutes(conn) -> None:
    gid = frame(conn, 1, T0)
    phone(conn, T0 - timedelta(minutes=42), "prev", place="Eastvale", lat=30.0)
    phone(conn, T0 + timedelta(hours=2), "next", place="Harbourton")
    out = suggest(conn, gid)
    near = [s for s in out if s["source_ref"] == "prev"][0]
    assert near["confidence"] == "far" and near["score"] == 0.4  # two-sided evidence, own tier


def test_case_d_stayed_put_interpolation_is_raised_and_first(conn) -> None:
    gid = frame(conn, 1, T0)
    phone(conn, T0 - timedelta(minutes=67), "a")
    phone(conn, T0 + timedelta(minutes=88), "b")
    out = suggest(conn, gid)
    assert [s["source"] for s in out] == ["interpolated", "iphone", "iphone"]
    assert out[0]["confidence"] == "low" and out[0]["score"] == 0.6  # larger gap 88 min = far, raised one tier
    assert out[0]["ends"]["stayed"] and out[0]["ends"]["before_s"] == -67 * 60 and out[0]["ends"]["after_s"] == 88 * 60
    assert [s["confidence"] for s in out[1:]] == ["far", "far"]


def test_case_e_far_apart_interpolation_is_lowered(conn) -> None:
    gid = frame(conn, 1, T0)
    phone(conn, T0 - timedelta(minutes=67), "a", place="Harbourton", lat=10.0)
    phone(conn, T0 + timedelta(minutes=88), "b", place="Eastvale", lat=10.3)  # about 33 km
    out = suggest(conn, gid)
    interp = [s for s in out if s["source"] == "interpolated"][0]
    assert interp["confidence"] == "weak" and not interp["ends"]["stayed"] and 30 < interp["ends"]["km"] < 36
    assert out[0]["source"] == "iphone" and out[0]["source_ref"] == "a"  # the nearer single point first


def test_session_rows_dedupe_by_place(conn) -> None:
    gid = frame(conn, 1, T0)
    frame(conn, 2, T0 + timedelta(minutes=14))
    phone(conn, T0 + timedelta(minutes=14, seconds=300), "a")
    phone(conn, T0 + timedelta(minutes=14, seconds=100), "b")  # same place, closer to the frame
    out = [s for s in suggest(conn, gid, window_s=600, limit=10) if s["source"] == "session"]
    assert [s["source_ref"] for s in out] == ["b"]


def test_stayed_put_by_distance_or_place(conn) -> None:
    gid = frame(conn, 1, T0)
    phone(conn, T0 - timedelta(hours=2), "a", place="Harbourton", lat=10.0)
    phone(conn, T0 + timedelta(hours=2), "b", place="Eastvale", lat=10.001)  # 111 m, different names
    assert [s["source"] for s in suggest(conn, gid)][0] == "interpolated"
    third = frame(conn, 3, T0 + timedelta(days=4))
    phone(conn, T0 + timedelta(days=4, hours=-2), "e", place="Harbourton", lat=10.0)
    phone(conn, T0 + timedelta(days=4, hours=2), "f", place="Eastvale", lat=10.5)  # 55 km, different places
    interp = [s for s in suggest(conn, third) if s["source"] == "interpolated"][0]
    assert not interp["ends"]["stayed"]
