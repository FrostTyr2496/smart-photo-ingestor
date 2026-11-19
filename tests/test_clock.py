"""Clock suggestions (sp-7): synthetic phone track and camera frames, invented data only."""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta

import pytest

from photovault import clock
from photovault.catalog import db, repo
from photovault.geo import utc_text

T0 = datetime(2026, 7, 12, 8, 0, tzinfo=UTC)


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "c.sqlite")
    db.migrate(c)
    yield c
    c.close()


def moments(start=T0, n=60, seed=7):
    """Sparse, irregular moments over ~5 days (phone fixes come from photos, so they are not a dense track)."""
    rng = random.Random(seed)
    return sorted(start + timedelta(minutes=rng.randrange(0, 5 * 24 * 60)) for _ in range(n))


def phone(conn, start=T0, offset="+01:00", tag="p", source="iphone", seed=7):
    pts = moments(start, seed=seed)
    for i, when in enumerate(pts):
        repo.upsert_geo_point(conn, utc_time=utc_text(when), lat=10.0, lon=20.0, alt=None, source=source,
                              source_ref=f"{tag}{i}", local_offset=offset)
    return pts


def frames(conn, body, *, start=T0, off_h=0.0, serial="S1", base=1, seed=7):
    """Frames taken at the phone's moments, whose camera clock (UTC) reads `off_h` hours too late."""
    bid = repo.get_or_create_body(conn, "Z 6II", serial, body)
    ids = []
    for i, when in enumerate(moments(start, seed=seed)[:50]):
        when = when + timedelta(minutes=2, hours=off_h)
        ids.append(repo.insert_group(
            conn, body_id=bid, shutter_count=base + i, capture_time=when.strftime("%Y-%m-%dT%H:%M:%S"),
            capture_offset="+00:00", time_source="exif", canonical_stem=f"{body}{base + i}",
            original_stem=f"O{base + i}", original_num=i, shoot_id=None))
    return bid, ids


def test_eight_hours_off(conn):
    phone(conn)
    bid, ids = frames(conn, "Zed", off_h=8)
    out = clock.suggest_offsets(conn)
    assert len(out) == 1
    s = out[0]
    assert s["body_id"] == bid and s["body"] == "Zed"
    assert s["delta_s"] == -8 * 3600          # frames read 8 h late, so shift back
    assert s["groups"] == [{"time_shift_s": -8 * 3600, "group_ids": sorted(s["groups"][0]["group_ids"])}]
    assert set(s["groups"][0]["group_ids"]) <= set(ids) and s["frames"] == len(s["groups"][0]["group_ids"])
    assert s["matches"] >= 20 and s["runner_up"] * 2 <= s["matches"]


def test_current_shift_is_added(conn):
    phone(conn)
    _, ids = frames(conn, "Zed", off_h=8)
    repo.update_group_meta(conn, ids, fields={"time_shift_s": -3600})
    s = clock.suggest_offsets(conn)[0]
    assert s["delta_s"] == -7 * 3600 and s["current_shifts"] == [-3600]
    assert s["groups"][0]["time_shift_s"] == -8 * 3600


def test_no_signal_cases(conn):
    frames(conn, "Zed", off_h=8)
    assert clock.suggest_offsets(conn) == []          # no phone points
    phone(conn, start=T0 + timedelta(days=60), seed=9)        # a track far from the frames
    assert clock.suggest_offsets(conn) == []


def test_random_times_give_nothing(conn):
    phone(conn)
    rng = random.Random(4)
    bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
    for i in range(80):
        when = T0 + timedelta(minutes=rng.randrange(0, 60 * 24 * 3))
        repo.insert_group(conn, body_id=bid, shutter_count=i + 1, capture_time=when.strftime("%Y-%m-%dT%H:%M:%S"),
                          capture_offset="+00:00", time_source="exif", canonical_stem=f"r{i}",
                          original_stem=f"r{i}", original_num=i, shoot_id=None)
    assert clock.suggest_offsets(conn) == []


def test_correct_frames_give_nothing(conn):
    phone(conn)
    frames(conn, "Zed", off_h=0)
    assert clock.suggest_offsets(conn) == []


def test_two_bodies_independent(conn):
    phone(conn)
    frames(conn, "Zed", off_h=8, serial="S1")
    frames(conn, "Eff", off_h=0, serial="S2", base=1)
    bid3, _ = frames(conn, "Gee", off_h=-5.5, serial="S3")
    out = {s["body"]: s for s in clock.suggest_offsets(conn)}
    assert set(out) == {"Zed", "Gee"}
    assert out["Zed"]["delta_s"] == -8 * 3600 and out["Gee"]["delta_s"] == int(5.5 * 3600)


def test_runs_split_by_local_offset(conn):
    phone(conn, offset="+01:00", tag="a")
    second = T0 + timedelta(days=10)
    phone(conn, start=second, offset="+09:00", tag="b", seed=7)
    frames(conn, "Zed", start=T0, off_h=8)                    # first trip: 8 h off
    frames(conn, "Zed", start=second, off_h=0, base=1000, seed=7)     # second trip: fine
    out = clock.suggest_offsets(conn)
    assert len(out) == 1 and out[0]["first_date"].startswith("2026-07-12") and out[0]["delta_s"] == -8 * 3600


def test_runtime_realistic_size(conn):
    rng = random.Random(1)
    with repo.transaction(conn):
        for i in range(20000):
            repo.upsert_geo_point(conn, utc_time=utc_text(T0 + timedelta(minutes=30 * i)), lat=1.0, lon=2.0,
                                  alt=None, source="iphone", source_ref=str(i), local_offset="+01:00")
        bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
        for i in range(50000):
            when = T0 + timedelta(minutes=rng.randrange(0, 30 * 20000))
            repo.insert_group(conn, body_id=bid, shutter_count=i + 1, capture_time=when.strftime("%Y-%m-%dT%H:%M:%S"),
                              capture_offset="+00:00", time_source="exif", canonical_stem=f"r{i}",
                              original_stem=f"r{i}", original_num=i, shoot_id=None)
    t = time.time()
    clock.suggest_offsets(conn)
    print("suggest_offsets runtime", time.time() - t)


def _burst(conn, bid, start, n, base, step_s=2):
    for i in range(n):
        when = start + timedelta(seconds=step_s * i)
        repo.insert_group(conn, body_id=bid, shutter_count=base + i, capture_time=when.strftime("%Y-%m-%dT%H:%M:%S"),
                          capture_offset="+00:00", time_source="exif", canonical_stem=f"b{base + i}",
                          original_stem=f"b{base + i}", original_num=i, shoot_id=None)


def _fix(conn, when, tag):
    repo.upsert_geo_point(conn, utc_time=utc_text(when), lat=10.0, lon=20.0, alt=None, source="iphone",
                          source_ref=tag, local_offset="+01:00")


def test_one_timelapse_one_phone_photo_gives_nothing(conn):
    _fix(conn, T0, "a")
    _fix(conn, T0 + timedelta(days=1), "b")
    bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
    _burst(conn, bid, T0 + timedelta(hours=4, minutes=15), 1000, 1)     # 1,000 frames, one session
    assert clock.suggest_offsets(conn) == []


def test_three_outings_eight_hours_off(conn):
    bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
    for k in range(3):
        at = T0 + timedelta(days=k)
        _fix(conn, at, f"p{k}")
        _burst(conn, bid, at + timedelta(hours=8, minutes=1), 30, 1 + 100 * k)
    s = clock.suggest_offsets(conn)
    assert len(s) == 1 and s[0]["delta_s"] == -8 * 3600
    assert s[0]["sessions_matched"] == 3 and s[0]["sessions_total"] == 3


def test_quarter_hour_never_offered(conn):
    bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
    for k in range(4):
        at = T0 + timedelta(days=k)
        _fix(conn, at, f"p{k}")
        _burst(conn, bid, at + timedelta(hours=4, minutes=16), 5, 1 + 100 * k)
    assert all(s["delta_s"] % 900 == 0 and abs(s["delta_s"]) % 3600 in (0, 1800, 2700) for s in clock.suggest_offsets(conn))
    assert clock.suggest_offsets(conn) == []


def test_shift_zero_already_matching(conn):
    bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
    for k in range(5):
        at = T0 + timedelta(days=k)
        _fix(conn, at, f"p{k}")
        _burst(conn, bid, at + timedelta(minutes=1), 5, 1 + 100 * k)
    for k in range(3):   # extra sessions that line up with some other shift
        _fix(conn, T0 + timedelta(days=10 + k), f"q{k}")
        _burst(conn, bid, T0 + timedelta(days=10 + k, hours=8), 5, 900 + 100 * k)
    assert clock.suggest_offsets(conn) == []


def test_accepted_correction_stays_applied_and_resolves_the_suggestion(conn):
    phone(conn)
    bid, ids = frames(conn, "Zed", off_h=8)
    s = clock.suggest_offsets(conn)[0]
    assert s["body_id"] == bid and s["start_utc"] < s["end_utc"]
    repo.add_body_clock(conn, body_id=bid, start_utc=s["start_utc"], end_utc=s["end_utc"],
                        correction_s=s["delta_s"], source="phone")
    assert clock.suggest_offsets(conn) == []  # the instants now line up with the phone
