"""The two-time model (sh-0b, D-080): instant with provenance, body clock corrections, the wall clock. Invented data."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from photovault import geo, timemodel
from photovault.catalog import db, repo
from photovault.timemodel import BodyClocks, instant, instant_info, wall_clock


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "c.sqlite")
    db.migrate(c)
    yield c
    c.close()


def row(t="2026-07-12T10:00:00", off=None, shift=0, body_id=1, **more):
    return {"capture_time": t, "capture_offset": off, "time_shift_s": shift, "body_id": body_id, **more}


def at(h, m=0):
    return datetime(2026, 7, 12, h, m, tzinfo=UTC)


def clocks(*rows):
    return BodyClocks(list(rows))


def test_exif_offset_provenance():
    when, prov = instant(row(off="-04:00"), default_offset="+00:00")
    assert when == at(14) and prov == "exif_offset"


def test_assumed_offset_provenance():
    when, prov = instant(row(), default_offset="+02:00")
    assert when == at(8) and prov == "assumed_offset"


def test_no_capture_time():
    assert instant(row(t=None), default_offset="+00:00") is None


def test_gps_is_never_reported_without_a_record():
    for r in (row(), row(off="+01:00", time_source="exif")):
        assert instant(r, default_offset="+00:00")[1] != "gps"


def test_aligned_when_a_body_clock_covers_the_instant():
    c = clocks((1, at(13), at(15), 3600))
    info = instant_info(row(off="-04:00"), "+00:00", c)  # 14:00 UTC before the correction: inside
    assert info.when == at(15) and info.provenance == "aligned" and info.correction_s == 3600 and not info.user_shift


def test_body_clock_outside_range_or_other_body_does_not_apply():
    out_of_range = clocks((1, at(15), at(16), 3600))
    other_body = clocks((2, at(13), at(15), 3600))
    for c in (out_of_range, other_body):
        info = instant_info(row(off="-04:00"), "+00:00", c)
        assert info.when == at(14) and info.provenance == "exif_offset"


def test_range_is_tested_on_the_instant_before_the_correction():
    c = clocks((1, at(13), at(14), 7200))
    assert instant(row(off="-04:00"), default_offset="+00:00", clocks=c)[0] == at(16)  # 14:00 is inside
    assert instant(row(t="2026-07-12T10:00:01", off="-04:00"), default_offset="+00:00", clocks=c)[0].hour == 14


def test_manual_shift_combines_with_a_correction():
    c = clocks((1, at(13), at(15), 3600))
    info = instant_info(row(off="-04:00", shift=120), "+00:00", c)  # 14:02 pre-correction, covered
    assert info.when == datetime(2026, 7, 12, 15, 2, tzinfo=UTC)
    assert info.provenance == "aligned" and info.user_shift


def test_user_shift_flag_without_correction():
    info = instant_info(row(off="-04:00", shift=-90), "+00:00", None)
    assert info.user_shift and info.provenance == "exif_offset"


def test_load_reads_only_accepted_rows(conn):
    bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
    repo.add_body_clock(conn, body_id=bid, start_utc="2026-07-12T00:00:00+00:00", end_utc="2026-07-12T23:59:59+00:00",
                        correction_s=600, source="phone")
    repo.add_body_clock(conn, body_id=bid, start_utc="2026-07-12T00:00:00+00:00", end_utc="2026-07-12T23:59:59+00:00",
                        correction_s=9999, source="user", accepted=False)
    c = BodyClocks.load(conn)
    assert c.correction(bid, at(12)) == 600 and c.correction(bid + 1, at(12)) is None


def test_group_utc_delegates_and_keeps_its_signature():
    r = row(off="-04:00", shift=60)
    assert geo.group_utc(r, "+00:00") == datetime(2026, 7, 12, 14, 1, tzinfo=UTC)
    assert geo.group_utc(r, "+00:00", clocks((1, at(13), at(15), 3600))) == datetime(2026, 7, 12, 15, 1, tzinfo=UTC)
    assert geo.group_utc(row(t=None), "+00:00") is None


def test_wall_clock_without_and_with_a_shoot_offset():
    r = row(off="-04:00")
    when = instant(r, default_offset="+00:00")[0]  # 14:00 UTC
    assert wall_clock(r, when) == "2026-07-12T10:00:00"  # as the camera wrote it
    assert wall_clock({**r, "shoot_local_offset": "-07:00"}, when) == "2026-07-12T07:00:00"
    assert wall_clock({**r, "shoot_local_offset": "+10:00"}, when) == "2026-07-13T00:00:00"  # the date moves
    assert wall_clock(row(t=None), None) is None


_n = [0]


def _group(conn, bid, t, shoot_id=None, off="-04:00"):
    _n[0] += 1
    return repo.insert_group(conn, body_id=bid, shutter_count=_n[0], capture_time=t,
                             capture_offset=off, time_source="exif", canonical_stem=t, original_stem=t,
                             original_num=1, shoot_id=shoot_id)


def test_group_wall_clock_reads_the_shoot_offset(conn):
    bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
    sid = repo.get_or_create_shoot(conn, name="Harbour Moonrise", shoot_date="2026-07-12", folder_rel="x")
    g1 = _group(conn, bid, "2026-07-12T22:00:00", sid)  # 02:00 UTC next day
    assert timemodel.group_wall_clock(conn, g1, "+00:00") == "2026-07-12T22:00:00"
    conn.execute("UPDATE shoots SET local_offset = '+10:00' WHERE shoot_id = ?", (sid,))
    assert timemodel.group_wall_clock(conn, g1, "+00:00") == "2026-07-13T12:00:00"


def test_photo_days_group_by_wall_date(conn):
    bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
    sid = repo.get_or_create_shoot(conn, name="Harbour Moonrise", shoot_date="2026-07-12", folder_rel="x")
    plain = _group(conn, bid, "2026-07-12T22:00:00")
    inshoot = _group(conn, bid, "2026-07-12T22:30:00", sid)  # 02:30 UTC the 13th, 12:30 in +10:00
    days = {d["day"]: d["groups"] for d in repo.list_photo_days(conn)}
    assert days == {"2026-07-12": 2}
    conn.execute("UPDATE shoots SET local_offset = '+10:00' WHERE shoot_id = ?", (sid,))
    days = [(d["day"], d["groups"]) for d in repo.list_photo_days(conn)]
    assert days == [("2026-07-13", 1), ("2026-07-12", 1)]
    on13 = [r["group_id"] for r in repo.list_groups(conn, day="2026-07-13")]
    on12 = [r["group_id"] for r in repo.list_groups(conn, day="2026-07-12")]
    assert on13 == [inshoot] and on12 == [plain]


def test_session_and_suggest_follow_the_correction(conn):
    bid = repo.get_or_create_body(conn, "Z 6II", "S1", "Zed")
    g = _group(conn, bid, "2026-07-12T10:00:00")
    repo.upsert_geo_point(conn, utc_time="2026-07-12T15:00:30+00:00", lat=1.0, lon=2.0, alt=None, source="phone",
                          source_ref="p")
    assert geo.suggest(conn, g, extended=False) == []  # 14:00 UTC is an hour from the fix
    repo.add_body_clock(conn, body_id=bid, start_utc="2026-07-12T13:00:00+00:00", end_utc="2026-07-12T15:00:00+00:00",
                        correction_s=3600, source="phone")
    found = geo.suggest(conn, g, extended=False)
    assert len(found) == 1 and found[0]["confidence"] == "high"
    assert geo.session_of(conn, g)[0]["utc"] == datetime(2026, 7, 12, 15, 0, tzinfo=UTC)
