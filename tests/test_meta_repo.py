"""ui-1: catalog migration 2 and the group metadata, geo point and settings repo functions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from photovault.catalog import db, migrations, repo


@pytest.fixture
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "catalog.sqlite")
    db.migrate(c)
    yield c
    c.close()


def _vol(conn, volume_id="w", role="WORKING"):
    repo.upsert_volume(conn, volume_id=volume_id, role=role, label="L", fs_type="exfat", da_volume_uuid=None,
                       marker_id=None, capacity_bytes=1000, mount_path=None)


def _group(conn, stem="DSC_0001", capture_time="2026-05-01T10:00:00", shoot_id=None, body_id=None):
    return repo.insert_group(conn, body_id=body_id, shutter_count=None, capture_time=capture_time,
                             capture_offset="+10:00", time_source="exif", canonical_stem=stem,
                             original_stem=stem, original_num=None, shoot_id=shoot_id)


def _primary(conn, group_id, sha, *, rel_path, volume_id, state="verified", role="primary"):
    """A primary asset of ``group_id`` with one instance on ``volume_id``."""
    aid = repo.insert_asset(conn, sha256=sha, size=10, partial_sha256="p" * 64, kind="raw",
                            group_id=group_id, group_role=role)
    repo.upsert_instance(conn, volume_id=volume_id, rel_path=rel_path, size=10, mtime_ns=1, kind="raw",
                         state=state, asset_id=aid, verified_sha256=sha if state == "verified" else None)
    return aid


def _ids(rows):
    return [r["group_id"] for r in rows]


# --- migration ----------------------------------------------------------------------------------------------


def test_migrate_gives_latest_version(tmp_path: Path) -> None:
    c = db.connect(tmp_path / "m.sqlite")
    try:
        assert db.migrate(c) == 5 == len(migrations.MIGRATIONS)
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"group_meta", "geo_points", "settings"} <= tables
        assert db.migrate(c) == 5
    finally:
        c.close()


def test_v1_is_unchanged() -> None:
    assert migrations.MIGRATIONS[0].lstrip().startswith("CREATE TABLE volumes")


# --- group meta: camera GPS ---------------------------------------------------------------------------------


def test_set_camera_gps_fills_effective_only_when_empty(conn) -> None:
    _vol(conn)
    gid = _group(conn)
    repo.set_camera_gps(conn, gid, lat=-33.85, lon=151.21, alt=12.0)
    row = repo.get_group_meta(conn, gid)
    assert (row["cam_lat"], row["lat"], row["lon"], row["alt"]) == (-33.85, -33.85, 151.21, 12.0)
    assert row["gps_source"] == "camera" and row["dirty"] == 0

    # A user's effective geotag is kept; the camera value is still recorded.
    repo.update_group_meta(conn, [gid], fields={"lat": 1.0, "lon": 2.0, "gps_source": "map"})
    repo.set_camera_gps(conn, gid, lat=-40.0, lon=170.0, alt=None)
    row = repo.get_group_meta(conn, gid)
    assert (row["cam_lat"], row["lat"], row["lon"], row["gps_source"]) == (-40.0, 1.0, 2.0, "map")


def test_set_camera_gps_without_fix_leaves_effective_empty(conn) -> None:
    gid = _group(conn)
    repo.set_camera_gps(conn, gid, lat=None, lon=None, alt=None)
    row = repo.get_group_meta(conn, gid)
    assert row is not None and row["lat"] is None and row["gps_source"] is None


# --- group meta: updates and keywords -----------------------------------------------------------------------


def test_update_keywords_add_remove_set_dedupe_case_insensitive(conn) -> None:
    gid = _group(conn)
    repo.update_group_meta(conn, [gid], fields={"keywords_set": ["Beach", " sunset ", "", "beach"]})
    assert json.loads(repo.get_group_meta(conn, gid)["keywords_json"]) == ["Beach", "sunset"]

    repo.update_group_meta(conn, [gid], fields={"keywords_add": ["SUNSET", "Boats ", "  "]})
    assert json.loads(repo.get_group_meta(conn, gid)["keywords_json"]) == ["Beach", "Boats", "sunset"]

    repo.update_group_meta(conn, [gid], fields={"keywords_remove": ["beach"]})
    assert json.loads(repo.get_group_meta(conn, gid)["keywords_json"]) == ["Boats", "sunset"]


def test_update_sets_fields_and_dirty_on_every_group(conn) -> None:
    g1, g2 = _group(conn, "A"), _group(conn, "B")
    changed = repo.update_group_meta(conn, [g1, g2, g1], fields={"creator": "Mush", "time_shift_s": 3600})
    assert changed == 2
    for gid in (g1, g2):
        row = repo.get_group_meta(conn, gid)
        assert row["creator"] == "Mush" and row["time_shift_s"] == 3600
        assert row["dirty"] == 1 and row["updated_at"]


def test_update_unknown_key_raises_and_writes_nothing(conn) -> None:
    gid = _group(conn)
    with pytest.raises(ValueError):
        repo.update_group_meta(conn, [gid], fields={"creator": "x", "bogus": 1})
    assert repo.get_group_meta(conn, gid) is None


def test_update_rejects_bad_gps_source_via_check(conn) -> None:
    gid = _group(conn)
    with pytest.raises(Exception):
        repo.update_group_meta(conn, [gid], fields={"gps_source": "nope"})


# --- geo points and settings --------------------------------------------------------------------------------


def test_geo_point_upsert_and_range(conn) -> None:
    repo.upsert_geo_point(conn, utc_time="2026-05-01T10:00:00Z", lat=1.0, lon=2.0, alt=None,
                          source="drone", source_ref="DJI_1.JPG")
    repo.upsert_geo_point(conn, utc_time="2026-05-01T10:30:00Z", lat=1.5, lon=2.5, alt=3.0,
                          source="drone", source_ref="DJI_2.JPG")
    repo.upsert_geo_point(conn, utc_time="2026-05-01T12:00:00Z", lat=9.0, lon=9.0, alt=None,
                          source="drone", source_ref="DJI_3.JPG")
    # Same (source, source_ref) updates in place.
    repo.upsert_geo_point(conn, utc_time="2026-05-01T10:05:00Z", lat=1.1, lon=2.1, alt=None,
                          source="drone", source_ref="DJI_1.JPG")
    assert conn.execute("SELECT COUNT(*) FROM geo_points").fetchone()[0] == 3

    rows = repo.geo_points_between(conn, "2026-05-01T10:00:00Z", "2026-05-01T11:00:00Z")
    assert [r["source_ref"] for r in rows] == ["DJI_1.JPG", "DJI_2.JPG"]
    assert rows[0]["lat"] == 1.1 and rows[0]["utc_time"] == "2026-05-01T10:05:00Z"
    assert repo.geo_points_between(conn, "2030-01-01T00:00:00Z", "2030-01-02T00:00:00Z") == []


def test_settings_get_set(conn) -> None:
    assert repo.get_setting(conn, "creator") is None
    assert repo.get_setting(conn, "creator", "fallback") == "fallback"
    repo.set_setting(conn, "creator", "A")
    repo.set_setting(conn, "creator", "B")
    assert repo.get_setting(conn, "creator") == "B"
    assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 1


# --- listings and counts ------------------------------------------------------------------------------------


@pytest.fixture
def seeded(conn):
    """Four groups on two volumes. g1 is complete, g2 lacks GPS, g3 lacks everything, g4 has no instance."""
    _vol(conn, "w", "WORKING")
    _vol(conn, "m", "MASTER")
    shoot_a = repo.get_or_create_shoot(conn, name="Trip", shoot_date="2026-05-01", folder_rel="t")
    shoot_b = repo.get_or_create_shoot(conn, name="Home", shoot_date="2026-06-01", folder_rel="h")
    body = repo.get_or_create_body(conn, "Z6II", "S1", "Z6")
    g1 = _group(conn, "DSC_1", "2026-05-01T09:00:00", shoot_a, body)
    g2 = _group(conn, "DSC_2", "2026-05-01T10:00:00", shoot_a, body)
    g3 = _group(conn, "DSC_3", "2026-06-01T08:00:00", shoot_b, None)
    g4 = _group(conn, "DSC_4", "2026-06-01T09:00:00", shoot_b, None)
    _primary(conn, g1, "1" * 64, rel_path="DCIM/DSC_1.NEF", volume_id="w")
    _primary(conn, g1, "1" * 64, rel_path="DCIM/DSC_1.NEF", volume_id="m")  # same file, second copy
    _primary(conn, g2, "3" * 64, rel_path="DCIM/DSC_2.NEF", volume_id="w", state="indexed")
    _primary(conn, g3, "4" * 64, rel_path="DCIM/DSC_3.NEF", volume_id="m", state="verified")
    repo.insert_asset(conn, sha256="5" * 64, size=1, partial_sha256="q" * 64, kind="raw", group_id=g4,
                      group_role="primary")  # primary with no instance
    repo.set_camera_gps(conn, g1, lat=1.0, lon=1.0, alt=None)
    repo.update_group_meta(conn, [g1], fields={"creator": "Mush", "keywords_set": ["x"]})
    repo.update_group_meta(conn, [g2], fields={"creator": "Mush"})
    return {"g1": g1, "g2": g2, "g3": g3, "g4": g4, "shoot_a": shoot_a, "shoot_b": shoot_b}


def test_list_groups_rows_and_primary_location(conn, seeded) -> None:
    rows = repo.list_groups(conn)
    assert _ids(rows) == [seeded["g1"], seeded["g2"], seeded["g3"], seeded["g4"]]
    first = rows[0]
    assert first["body_nickname"] == "Z6" and first["shoot_name"] == "Trip" and first["shoot_date"] == "2026-05-01"
    assert first["lat"] == 1.0 and first["creator"] == "Mush"
    assert first["rel_path"] == "DCIM/DSC_1.NEF" and first["volume_id"] == "w"  # WORKING preferred
    assert rows[2]["body_nickname"] is None and rows[2]["lat"] is None and rows[2]["creator"] is None
    assert rows[3]["rel_path"] is None and rows[3]["volume_id"] is None


def test_list_groups_missing_filters(conn, seeded) -> None:
    assert _ids(repo.list_groups(conn, missing="gps")) == [seeded["g2"], seeded["g3"], seeded["g4"]]
    assert _ids(repo.list_groups(conn, missing="creator")) == [seeded["g3"], seeded["g4"]]
    assert _ids(repo.list_groups(conn, missing="keywords")) == [seeded["g2"], seeded["g3"], seeded["g4"]]
    assert _ids(repo.list_groups(conn, missing="any")) == [seeded["g2"], seeded["g3"], seeded["g4"]]
    assert _ids(repo.list_groups(conn, missing="creator", shoot_id=seeded["shoot_b"])) == [
        seeded["g3"], seeded["g4"]]
    with pytest.raises(ValueError):
        repo.list_groups(conn, missing="weather")


def test_list_groups_volume_filter_and_paging(conn, seeded) -> None:
    assert _ids(repo.list_groups(conn, volume_id="m")) == [seeded["g1"], seeded["g3"]]
    on_master = repo.list_groups(conn, volume_id="m")
    assert on_master[0]["volume_id"] == "m" and on_master[0]["rel_path"] == "DCIM/DSC_1.NEF"
    assert _ids(repo.list_groups(conn, limit=2)) == [seeded["g1"], seeded["g2"]]
    assert _ids(repo.list_groups(conn, limit=2, offset=2)) == [seeded["g3"], seeded["g4"]]


def test_group_index_used_for_primary_lookup(conn) -> None:
    plan = " ".join(r[3] for r in conn.execute(
        "EXPLAIN QUERY PLAN SELECT asset_id FROM assets WHERE group_id = 1 AND group_role = 'primary'"))
    assert "assets_group" in plan


def test_list_groups_day_filter(conn, seeded) -> None:
    assert _ids(repo.list_groups(conn, day="2026-06-01")) == [seeded["g3"], seeded["g4"]]
    assert _ids(repo.list_groups(conn, day="2026-06-01", missing="gps", limit=1)) == [seeded["g3"]]
    assert repo.list_groups(conn, day="2020-01-01") == []
    undated = _group(conn, "DSC_9", None)
    assert _ids(repo.list_groups(conn, day=repo.UNDATED)) == [undated]
    with pytest.raises(ValueError):
        repo.list_groups(conn, day="June 1")


def _unhashed(conn, volume_id, rel_path, group_id, role="primary"):
    """A file the volume's index saw and grouped, not hashed yet: an instance with no asset, and a job item."""
    repo.upsert_instance(conn, volume_id=volume_id, rel_path=rel_path, size=10, mtime_ns=1, kind="raw", state="indexed")
    job = repo.create_job(conn, kind="index_volume", source_volume_id=volume_id, target_volume_id=None, params={})
    repo.add_job_items(conn, job, [{"seq": 1, "src_rel": rel_path, "dst_rel": None, "group_id": group_id, "role": role}])


def test_unhashed_groups_are_found_through_their_index_job(conn, seeded) -> None:
    _vol(conn, "leg", "LEGACY")
    g5 = _group(conn, "DSC_5", "2026-07-01T08:00:00")
    _unhashed(conn, "leg", "Old/DSC_5.NEF", g5)
    _unhashed(conn, "leg", "Old/DSC_5.JPG", g5, role="jpeg")
    assert repo.indexed_primary_files(conn, [g5, seeded["g1"]]) == {g5: [{"volume_id": "leg", "rel_path": "Old/DSC_5.NEF"}]}
    assert _ids(repo.list_groups(conn, volume_id="leg")) == [g5]
    assert repo.list_photo_days(conn, volume_id="leg") == [{"day": "2026-07-01", "groups": 1, "bodies": []}]
    conn.execute("UPDATE instances SET state = 'missing' WHERE rel_path = 'Old/DSC_5.NEF'")
    assert repo.indexed_primary_files(conn, [g5]) == {}


def test_list_groups_time_range(conn, seeded) -> None:
    assert _ids(repo.list_groups(conn, after="2026-05-01T09:30:00", before="2026-06-01T08:00:00")) == [
        seeded["g2"], seeded["g3"]]
    assert _ids(repo.list_groups(conn, after="2026-06-01T08:00:01")) == [seeded["g4"]]
    _group(conn, "DSC_9", None)
    assert seeded["g4"] in _ids(repo.list_groups(conn, after="2000-01-01T00:00:00"))
    assert len(repo.list_groups(conn, after="2000-01-01T00:00:00")) == 4  # undated never matches a range
    with pytest.raises(ValueError):
        repo.list_groups(conn, after="2026-06-01")


def test_list_photo_days(conn, seeded) -> None:
    _group(conn, "DSC_9", None)
    assert repo.list_photo_days(conn) == [
        {"day": "2026-06-01", "groups": 2, "bodies": []},
        {"day": "2026-05-01", "groups": 2, "bodies": ["Z6"]},
        {"day": repo.UNDATED, "groups": 1, "bodies": []},
    ]
    assert repo.list_photo_days(conn, volume_id="m") == [
        {"day": "2026-06-01", "groups": 1, "bodies": []},
        {"day": "2026-05-01", "groups": 1, "bodies": ["Z6"]},
    ]
    assert [d["day"] for d in repo.list_photo_days(conn, missing="creator")] == ["2026-06-01", repo.UNDATED]


def test_missing_counts(conn, seeded) -> None:
    assert repo.missing_counts(conn) == {"groups": 4, "gps": 3, "creator": 2, "keywords": 3}
    assert repo.missing_counts(conn, volume_id="m") == {"groups": 2, "gps": 1, "creator": 1, "keywords": 1}
    empty = repo.missing_counts(conn, volume_id="nope")
    assert empty == {"groups": 0, "gps": 0, "creator": 0, "keywords": 0}


def test_list_shoots(conn, seeded) -> None:
    rows = {r["name"]: r for r in repo.list_shoots(conn)}
    assert rows["Trip"]["groups"] == 2 and rows["Trip"]["shoot_date"] == "2026-05-01"
    assert rows["Home"]["groups"] == 2
