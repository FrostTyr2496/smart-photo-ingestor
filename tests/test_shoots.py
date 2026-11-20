"""The shoot planner (sh-1, D-080) on synthetic catalogs. Names and places are invented."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from photovault import shoots
from photovault.catalog import db, repo

SPEC_SHOOT_KEYS = {"proposal_id", "trip_id", "name", "shoot_date", "start_instant", "end_instant", "wall",
                   "local_offset", "group_ids", "volumes", "counts", "sample_group_ids", "centroid", "points", "gaps", "flags",
                   "locked"}
SPEC_TRIP_KEYS = {"proposal_id", "name", "start_date", "end_date", "shoot_ids", "flags"}


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "c.sqlite")
    db.migrate(c)
    c.execute("INSERT INTO volumes (volume_id, role, label, created_at, last_seen_at) "
              "VALUES ('v1', 'WORKING', 'W', 'x', 'x')")
    yield c
    c.close()


class Builder:
    def __init__(self, conn):
        self.conn, self.n, self.bodies = conn, 0, {}

    def body(self, nick):
        if nick not in self.bodies:
            self.bodies[nick] = repo.get_or_create_body(self.conn, f"Model {nick}", nick, nick)
        return self.bodies[nick]

    def frames(self, start, count, step_s=60, *, body="A", off="+00:00", path=None, lat=None, lon=None, seq=None,
               shoot_id=None):
        """Frames at ``start`` (naive wall time as the camera wrote it) every ``step_s`` seconds."""
        ids = []
        for k in range(count):
            self.n += 1
            t = start + timedelta(seconds=k * step_s)
            self.conn.execute(
                "INSERT INTO capture_groups (group_id, body_id, shutter_count, capture_time, capture_offset, "
                "time_source, canonical_stem, original_stem, sequence_id, shoot_id, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (self.n, self.body(body), self.n, t.isoformat(), off, "exif", f"s{self.n}", f"o{self.n}", seq, shoot_id, "x"))
            if lat is not None:
                self.conn.execute("INSERT INTO group_meta (group_id, lat, lon, updated_at) VALUES (?,?,?,'x')",
                                  (self.n, lat, lon))
            if path:
                self.conn.execute("INSERT INTO assets (asset_id, sha256, size, partial_sha256, kind, group_id, group_role, "
                                  "first_seen_at) VALUES (?,?,?,?,?,?,?,?)",
                                  (self.n, f"h{self.n}", 1, "p", "raw", self.n, "primary", "x"))
                self.conn.execute("INSERT INTO instances (asset_id, volume_id, rel_path, rel_path_key, size, mtime_ns, "
                                  "kind, state, last_seen_at) VALUES (?,?,?,?,?,?,?,?,?)",
                                  (self.n, "v1", f"{path}/f{self.n}.nef", f"{path}/f{self.n}.nef".lower(), 1, 1, "raw",
                                   "indexed", "x"))
            ids.append(self.n)
        return ids

    def phone(self, start_utc, count, step_s=600, *, lat, lon, offset, place=None, city=None, state=None):
        for k in range(count):
            t = start_utc + timedelta(seconds=k * step_s)
            self.conn.execute(
                "INSERT INTO geo_points (utc_time, lat, lon, source, source_ref, local_offset, place_name, city, state) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (t.isoformat(), lat, lon, "iphone", f"{start_utc.isoformat()}-{k}-{lat}", offset, place, city, state))


def d(day, h=0, m=0, month=9):
    return datetime(2023, month, day, h, m)


@pytest.fixture
def b(conn):
    return Builder(conn)


def test_night_crossing_midnight_is_one_shoot_on_the_evening(conn, b):
    b.frames(d(9, 21), 200, step_s=60)  # 21:00 .. 00:19
    b.frames(d(10, 1), 200, step_s=60)  # 01:00 .. 04:19, gap well under 8 h
    p = shoots.plan(conn)
    assert len(p["shoots"]) == 1
    assert p["shoots"][0]["shoot_date"] == "2023-09-09"
    assert p["shoots"][0]["counts"]["frames"] == 400


def test_trip_of_three_days(conn, b):
    for day in (7, 8, 9):
        b.frames(d(day, 18), 30, lat=10.0 + day * 0.01, lon=20.0)
    p = shoots.plan(conn)
    assert len(p["shoots"]) == 3 and len(p["trips"]) == 1
    t = p["trips"][0]
    assert t["start_date"] == "2023-09-07" and t["end_date"] == "2023-09-09" and len(t["shoot_ids"]) == 3
    assert {s["trip_id"] for s in p["shoots"]} == {t["proposal_id"]}


def test_distant_days_are_not_a_trip(conn, b):
    b.frames(d(1, 18), 10)
    b.frames(d(20, 18), 10)
    p = shoots.plan(conn)
    assert len(p["shoots"]) == 2 and p["trips"] == [] and all(s["trip_id"] is None for s in p["shoots"])


def test_move_over_50km_with_a_2h_gap_splits(conn, b):
    b.frames(d(9, 20), 20, lat=10.0, lon=20.0)
    b.frames(d(9, 22, 10), 20, lat=10.0, lon=20.9)  # about 98 km east, gap 1 h 51 m
    assert len(shoots.plan(conn)["shoots"]) == 2


def test_move_with_short_gap_does_not_split(conn, b):
    b.frames(d(9, 20), 20, lat=10.0, lon=20.0)
    b.frames(d(9, 20, 30), 20, lat=10.0, lon=20.9)  # gap 11 min
    assert len(shoots.plan(conn)["shoots"]) == 1


def test_sequence_is_never_split(conn, b):
    b.frames(d(9, 20), 10, lat=10.0, lon=20.0)
    ids = b.frames(d(9, 22, 10), 10, lat=10.0, lon=20.9)
    conn.execute("INSERT INTO sequences (sequence_id, kind) VALUES (5, 'timelapse')")
    conn.execute("UPDATE capture_groups SET sequence_id = 5 WHERE group_id BETWEEN ? AND ?", (ids[0] - 1, ids[-1]))
    assert len(shoots.plan(conn)["shoots"]) == 1


def test_two_bodies_with_8h_offset_flag_mismatch(conn, b):
    b.frames(d(9, 20), 40, body="A")        # 20:00 - 20:39
    b.frames(d(10, 4), 40, body="B")        # the same event on a clock 8 h ahead; gap 7 h 21 m
    p = shoots.plan(conn)
    assert len(p["shoots"]) == 1
    assert "body_clock_mismatch" in p["shoots"][0]["flags"]
    assert p["shoots"][0]["counts"]["by_body"] == {"A": 40, "B": 40}


def test_locked_shoot_is_left_alone(conn, b):
    conn.execute("INSERT INTO shoots (shoot_id, name, shoot_date, created_at) "
                 "VALUES (1, 'Harbour Moonrise', '2023-09-09', 'x')")
    b.frames(d(9, 20), 20, shoot_id=1)
    b.frames(d(9, 20, 30), 20)  # unlocked frames right after
    p = shoots.plan(conn)
    locked = [s for s in p["shoots"] if s["locked"]]
    free = [s for s in p["shoots"] if not s["locked"]]
    assert len(locked) == 1 and locked[0]["name"]["value"] == "Harbour Moonrise"
    assert locked[0]["counts"]["frames"] == 20 and free[0]["counts"]["frames"] == 20
    assert p["summary"]["locked"] == 1


def test_name_from_dated_folder_with_alternatives(conn, b):
    b.frames(d(9, 20), 10, path="Photos/2023/20230909 - Harbour Moonrise/Z6", lat=1.0, lon=1.0)
    s = shoots.plan(conn)["shoots"][0]
    assert s["name"]["value"] == "Harbour Moonrise" and s["name"]["source"] == "folder"
    assert "weak_name" not in s["flags"]
    assert s["name"]["alternatives"][-1]["source"] == "fallback"


def test_dated_folder_below_half_is_not_used(conn, b):
    b.frames(d(9, 20), 4, path="Photos/20230909 - Harbour Moonrise")
    b.frames(d(9, 20, 10), 6, path="Photos/Dunes")
    s = shoots.plan(conn)["shoots"][0]
    assert s["name"]["value"] == "Dunes" and "weak_name" in s["flags"]


def test_name_from_phone_place_then_city(conn, b):
    b.frames(d(9, 20), 10)
    b.phone(datetime(2023, 9, 9, 19, 30, tzinfo=UTC), 6, lat=1.0, lon=1.0, offset="+00:00", place="Harbourton Pier",
            city="Harbourton")
    s = shoots.plan(conn)["shoots"][0]
    assert s["name"]["value"] == "Harbourton Pier" and s["name"]["source"] == "phone"
    assert s["name"]["alternatives"][0] == {"value": "Harbourton", "source": "phone"}
    conn.execute("UPDATE geo_points SET place_name = NULL")
    assert shoots.plan(conn)["shoots"][0]["name"]["value"] == "Harbourton"


def test_other_folder_stoplist_and_containers(conn, b):
    b.frames(d(9, 20), 10, path="DCIM/100NCZ_6/Card 2/Selected/Dunes Walk")
    s = shoots.plan(conn)["shoots"][0]
    assert s["name"]["value"] == "Dunes Walk" and s["name"]["source"] == "folder" and "weak_name" in s["flags"]
    repo.set_setting(conn, "shoot_name_stoplist", json.dumps(["dunes walk"]))
    s = shoots.plan(conn)["shoots"][0]
    assert s["name"]["source"] == "fallback" and s["name"]["value"] == "2023-09-09 · A"


def test_fallback_name(conn, b):
    b.frames(d(9, 20), 5)
    s = shoots.plan(conn)["shoots"][0]
    assert s["name"] == {"value": "2023-09-09 · A", "source": "fallback", "alternatives": []}
    assert "weak_name" in s["flags"]


def test_odd_glyphs_are_cleaned():
    assert shoots.clean_name("Harbour Moon​rise  ") == "Harbour Moonrise"
    dated, humans = shoots.folder_info("DCIM/2023-09-09 Harbour Moonrise/f.nef", set())
    assert dated == ("DCIM/2023-09-09 Harbour Moonrise", "Harbour Moonrise") and humans == []


def test_two_shoots_same_date_and_name_are_suffixed(conn, b):
    b.frames(d(9, 8), 5)
    b.frames(d(9, 20), 5)
    names = [s["name"]["value"] for s in shoots.plan(conn)["shoots"]]
    assert len(set(names)) == 2


def test_local_offset_proposal(conn, b):
    b.frames(d(9, 21), 20, off="-04:00", lat=1.0, lon=1.0)  # camera left on home time
    b.phone(datetime(2023, 9, 10, 0, 50, tzinfo=UTC), 8, lat=1.0, lon=1.0, offset="-07:00", state="Northland")
    p = shoots.plan(conn)
    lo = p["shoots"][0]["local_offset"]
    assert lo == {"value": "-07:00", "camera": "-04:00", "source": "phone", "changes": True}
    assert "tz_change" in p["shoots"][0]["flags"]
    assert p["shoots"][0]["wall"]["start"] == "2023-09-09T18:00:00-07:00"


def test_no_proposal_when_offsets_agree(conn, b):
    b.frames(d(9, 21), 20, off="-07:00")
    b.phone(datetime(2023, 9, 10, 4, 0, tzinfo=UTC), 8, lat=1.0, lon=1.0, offset="-07:00")
    lo = shoots.plan(conn)["shoots"][0]["local_offset"]
    assert lo["changes"] is False and lo["value"] == "-07:00"


def test_trip_name_uses_state_and_range(conn, b):
    for day in (9, 10, 11):
        b.frames(d(day, 18), 10, lat=1.0, lon=1.0)
    b.phone(datetime(2023, 9, 9, 17, tzinfo=UTC), 80, step_s=3600, lat=1.0, lon=1.0, offset="+00:00",
            state="Northland")
    t = shoots.plan(conn)["trips"][0]
    assert t["name"]["value"] == "Northland · 9–11 Sep" and t["name"]["source"] == "phone"
    assert shoots._date_range("2023-09-30", "2023-10-02") == "30 Sep–2 Oct"


def test_huge_timelapse_and_limits(conn, b):
    b.frames(d(9, 20), 1100, step_s=5, lat=1.0, lon=1.0)
    s = shoots.plan(conn)["shoots"][0]
    assert "huge_timelapse" in s["flags"] and len(s["sample_group_ids"]) <= 12 and len(s["points"]) <= 50
    assert len(s["gaps"]) <= 20


def test_multi_place_flag(conn, b):
    b.frames(d(9, 20), 10, lat=10.0, lon=20.0)
    b.frames(d(9, 20, 12), 10, lat=10.0, lon=20.5)  # about 55 km, but the gap is under 1 h so no split
    s = shoots.plan(conn)
    assert len(s["shoots"]) == 1 and "multi_place" in s["shoots"][0]["flags"]


def test_plan_json_has_every_spec_key_and_stable_ids(conn, b):
    for day in (7, 8):
        b.frames(d(day, 18), 10, lat=1.0, lon=1.0)
    p = shoots.plan(conn, now=datetime(2026, 11, 18, 21, 0, 0, tzinfo=UTC))
    assert p["plan_id"] == "shoots-20261118-210000"
    assert set(p) == {"plan_id", "created_at", "decisions_through", "params", "summary", "trips", "shoots",
                     "unassigned_sessions"}
    assert set(p["params"]) == {"gap_h", "split_km", "merge_h", "region_km", "from", "to"}
    assert set(p["summary"]) == {"frames", "shoots", "trips", "unassigned", "locked", "flagged"}
    assert SPEC_SHOOT_KEYS <= set(p["shoots"][0]) and SPEC_TRIP_KEYS <= set(p["trips"][0])
    assert p["shoots"][0]["proposal_id"].startswith("s:") and p["trips"][0]["proposal_id"].startswith("t:")
    assert [s["proposal_id"] for s in shoots.plan(conn)["shoots"]] == [s["proposal_id"] for s in p["shoots"]]
    json.dumps(p)


def test_from_to_and_unassigned(conn, b):
    b.frames(d(1, 18), 5)
    b.frames(d(20, 18), 5)
    conn.execute("INSERT INTO capture_groups (body_id, shutter_count, time_source, canonical_stem, original_stem, "
                 "created_at) VALUES (NULL, NULL, 'none', 'z', 'z', 'x')")
    p = shoots.plan(conn, frm="2023-09-10")
    assert len(p["shoots"]) == 1 and p["summary"]["unassigned"] == 1


def _decide(conn, pid, kind, **payload):
    conn.execute("INSERT INTO shoot_plan_decisions (proposal_id, kind, payload_json, decided_at) VALUES (?,?,?,'x')",
                 (pid, kind, json.dumps(payload)))


def test_decisions_replay_after_a_replan(conn, b):
    ids = b.frames(d(9, 20), 30, path="Photos/Dunes")
    b.frames(d(12, 20), 10)
    base = shoots.plan(conn)
    first = base["shoots"][0]["proposal_id"]
    _decide(conn, first, "split", after_group_id=ids[9])
    p = shoots.plan(conn)
    assert len(p["shoots"]) == 3
    half = p["shoots"][0]
    assert half["counts"]["frames"] == 10 and half["group_ids"][-1] == ids[9]
    _decide(conn, half["proposal_id"], "rename", value="Moonrise A")
    _decide(conn, half["proposal_id"], "accept")
    p2 = shoots.plan(conn)
    assert p2["shoots"][0]["name"]["value"] == "Moonrise A" and p2["shoots"][0]["state"] == "accepted"
    _decide(conn, half["proposal_id"], "merge", **{"with": "next"})
    p3 = shoots.plan(conn)
    assert len(p3["shoots"]) == 2 and p3["shoots"][0]["counts"]["frames"] == 30
    assert shoots.plan(conn)["shoots"] == p3["shoots"]


def test_rename_skip_reject_move(conn, b):
    b.frames(d(9, 20), 5, path="Photos/Dunes", lat=1.0, lon=1.0)
    b.frames(d(10, 20), 5, path="Photos/Dunes", lat=1.0, lon=1.0)
    b.frames(d(25, 20), 5)
    p = shoots.plan(conn)
    s0, s1, s2 = (s["proposal_id"] for s in p["shoots"])
    t = p["trips"][0]["proposal_id"]
    _decide(conn, s0, "rename", value="Harbour Moonrise")
    _decide(conn, s0, "accept")
    _decide(conn, s1, "skip")
    _decide(conn, s1, "reject_name", value="Dunes")
    _decide(conn, s2, "move", trip_id=t)
    _decide(conn, t, "rename", value="Northland")
    p = shoots.plan(conn)
    a, bb, c = p["shoots"]
    assert a["name"]["value"] == "Harbour Moonrise" and a["name"]["source"] == "user" and a["state"] == "accepted"
    assert bb["state"] == "skipped" and bb["name"]["source"] == "fallback"  # Dunes rejected everywhere
    assert c["trip_id"] == t and len(p["trips"][0]["shoot_ids"]) == 3 and p["trips"][0]["name"]["value"] == "Northland"


def test_write_plan_only_writes_under_home(conn, b, tmp_path):
    b.frames(d(9, 20), 5)
    path = shoots.write_plan(conn, tmp_path / "home", now=datetime(2026, 11, 18, 21, 0, 0, tzinfo=UTC))
    assert path == tmp_path / "home" / "plans" / "shoots-20261118-210000.json"
    assert json.loads(path.read_text())["summary"]["shoots"] == 1


def test_calibrate_on_known_folders(conn, b):
    b.frames(d(1, 8), 20, path="P/20230901 - Alpha")
    b.frames(d(1, 20), 20, path="P/20230901 - Bravo")     # 11 h 40 m after Alpha's last frame
    b.frames(d(2, 14), 20, path="P/20230902 - Charlie")   # 17 h after Bravo
    b.frames(d(2, 14, 30), 5)                             # unlabeled tail directly after Charlie
    out = shoots.calibrate(conn, gap_values=(4, 12, 24))
    assert out["ground_truth_folders"] == 3
    by = {r["gap_h"]: r for r in out["results"]}
    assert by[4]["shoots"] == 3 and by[4]["precision"] == 1.0 and by[4]["folders_whole"] == 3
    assert by[4]["folders_merged"] == 0
    assert by[24]["shoots"] == 1 and by[24]["folders_merged"] == 3 and by[24]["recall"] < 1.0
    assert by[12]["shoots"] == 2 and by[12]["folders_whole"] == 3 and by[12]["folders_merged"] == 2


def test_calibrate_counts_a_split_folder(conn, b):
    b.frames(d(1, 8), 20, path="P/20230901 - Alpha")
    b.frames(d(1, 20), 20, path="P/20230901 - Alpha")
    r = shoots.calibrate(conn, gap_values=(4,))["results"][0]
    assert r["folders_split"] == 1 and r["precision"] == 0.0


def test_generic_names_are_stoplisted_and_volumes_listed(conn, b):
    b.frames(d(9, 20), 10, path="Backup/New/Nikon Z6/240GB/W/Z6II/Dunes Walk")
    s = shoots.plan(conn)["shoots"][0]
    assert s["name"]["value"] == "Dunes Walk"
    assert s["volumes"] == [{"volume_id": "v1", "label": "W", "frames": 10}]
    b.frames(d(12, 20), 5, path="New")
    assert shoots.plan(conn)["shoots"][1]["name"]["source"] == "fallback"


def test_phone_place_beats_other_folder(conn, b):
    b.frames(d(9, 20), 10, path="Dunes Walk")
    b.phone(datetime(2023, 9, 9, 19, 30, tzinfo=UTC), 6, lat=1.0, lon=1.0, offset="+00:00", place="Harbourton Pier")
    n = shoots.plan(conn)["shoots"][0]["name"]
    assert n["value"] == "Harbourton Pier" and {"value": "Dunes Walk", "source": "folder"} in n["alternatives"]
