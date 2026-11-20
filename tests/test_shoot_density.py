"""sh-11: phone density and split suggestions on synthetic catalogs. Places and labels are invented."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from photovault import shoots
from tests.test_shoot_members import put_analysis
from tests.test_shoots import Builder, conn, d  # noqa: F401  (fixtures)


@pytest.fixture
def b(conn):  # noqa: F811
    return Builder(conn)


def two_outings(b, conn, day, *, venues=("Beach Cafe", "Old Mill"), far=True, labels=(("beach",), ("dinner",))):
    """20 frames, a 1.5 h gap, 20 frames; phone points either side say where and what."""
    b.frames(d(day, 9), 20)
    b.frames(d(day, 11, 30), 20)
    # the camera frames carry no GPS: the phone supplies it
    put_analysis(conn, datetime(2023, 9, day, 8, 30, tzinfo=UTC), 6, labels=labels[0], place=venues[0], lat=1.0, lon=1.0)
    put_analysis(conn, datetime(2023, 9, day, 11, 40, tzinfo=UTC), 6, labels=labels[1], place=venues[1],
                 lat=1.05 if far else 1.0, lon=1.05 if far else 1.0)


def test_dense_day_with_two_venues_is_suggested(conn, b):
    two_outings(b, conn, 9)
    s = shoots.plan(conn)["shoots"][0]
    assert s["phone_density"]["points"] == 12 and s["phone_density"]["per_hour"] > 0
    sug = s["split_suggestions"]
    assert len(sug) == 1 and sug[0]["score"] >= 0.5 and "may_split" in s["flags"]
    assert sug[0]["after_group_id"] == 20 and sug[0]["gap_s"] > 5000
    assert any("moved" in r for r in sug[0]["reasons"]) and len(sug[0]["reasons"]) <= 3
    assert any("labels change" in r for r in sug[0]["reasons"])


def test_quiet_day_with_one_venue_is_not(conn, b):
    two_outings(b, conn, 9, venues=("Beach Cafe", "Beach Cafe"), far=False, labels=(("beach",), ("beach",)))
    s = shoots.plan(conn)["shoots"][0]
    sug = s.get("split_suggestions", [])
    assert all(x["score"] < 0.5 for x in sug) and "may_split" not in s["flags"]


def test_camera_coast_jump_alone_is_suggested(conn, b):
    b.frames(d(9, 9), 20, lat=1.0, lon=1.0)
    b.frames(d(9, 11, 30), 20, lat=1.05, lon=1.05)  # about 7 km on
    s = shoots.plan(conn)["shoots"][0]
    assert s["split_suggestions"][0]["reasons"][0].startswith("camera moved")


def test_sparse_shoot_without_phone_or_gps_gets_nothing(conn, b):
    b.frames(d(9, 9), 20)
    b.frames(d(9, 11, 30), 20)
    s = shoots.plan(conn)["shoots"][0]
    assert "split_suggestions" not in s and "may_split" not in s["flags"]
    assert s["phone_density"] == {"points": 0, "per_hour": 0.0, "ratio_to_median": 0.0}


def test_density_ratio_to_median_and_places(conn, b):
    two_outings(b, conn, 9)                       # busy: 12 phone points
    b.frames(d(20, 9), 10)
    put_analysis(conn, datetime(2023, 9, 20, 8, 45, tzinfo=UTC), 2, labels=("hill",), place="Hill")  # quiet
    b.frames(d(25, 9), 10)
    put_analysis(conn, datetime(2023, 9, 25, 8, 45, tzinfo=UTC), 2, labels=("hill",), place="Hill")
    by = {s["shoot_date"]: s for s in shoots.plan(conn)["shoots"]}
    assert by["2023-09-08"]["phone_density"]["ratio_to_median"] > 1 >= by["2023-09-19"]["phone_density"]["ratio_to_median"]


def test_beta_keeps_faint_suggestions_and_full_run_drops_them(conn, b):
    b.frames(d(9, 9), 20)
    b.frames(d(9, 10, 0), 20)  # a 40 min gap
    put_analysis(conn, datetime(2023, 9, 9, 8, 30, tzinfo=UTC), 6, labels=("beach",), place="Beach Cafe")
    put_analysis(conn, datetime(2023, 9, 9, 10, 20, tzinfo=UTC), 6, labels=("beach",), place="Beach Cafe")
    s = shoots.plan(conn)["shoots"][0]
    assert all(x["score"] < 0.5 for x in s.get("split_suggestions", []))
