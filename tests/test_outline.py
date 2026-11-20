"""rv-46 (D-134): the trips outline, `GET /api/outline`, and the `shoot_key` filters. Names are invented."""

from __future__ import annotations

import json

import pytest

from photovault import outline
from photovault.catalog import db
from photovault.config import load_config
from tests.test_web_api import Client, srv  # noqa: F401  (the server fixture: groups a, b, c; a and c have files)


def _plan(ids: dict[str, int]) -> dict:
    return {
        "plan_id": "shoots-test",
        "trips": [{"proposal_id": "t:1", "name": {"value": "Coast week", "source": "phone"},
                   "start_date": "2026-11-10", "end_date": "2026-11-10", "shoot_ids": ["s:1"]}],
        "shoots": [{"proposal_id": "s:1", "trip_id": "t:1", "name": {"value": "Cove day", "source": "folder"},
                    "shoot_date": "2026-11-10", "wall": {"start": "2026-11-10T21:00:00", "end": "2026-11-10T21:00:00"},
                    "group_ids": [ids["b"], ids["c"]], "sample_group_ids": [ids["b"]],
                    "details": {"place": {"value": "Harbourton", "confidence": 0.8}, "subject": {"value": "Boats", "confidence": 0.4}},
                    "volumes": [{"volume_id": "vol-work", "label": "WORK", "frames": 2}], "locked": False}],
    }


def _apply_one(srv: Client) -> None:
    """Group a becomes an accepted shoot in an accepted trip."""
    conn = db.connect(load_config().catalog_path)
    conn.execute("INSERT INTO trips (trip_id, name, start_date, end_date, created_at) "
                 "VALUES (1, 'Dune trip', '2026-11-09', '2026-11-10', 'x')")
    conn.execute("INSERT INTO shoots (shoot_id, name, shoot_date, created_at, trip_id, place, subject) "
                 "VALUES (7, 'Dune day', '2026-11-10', 'x', 1, 'Dunes', 'Sunrise')")
    conn.execute("UPDATE capture_groups SET shoot_id = 7 WHERE group_id = ?", (srv.ids["a"],))
    conn.commit()
    conn.close()


def _conn():
    conn = db.connect(load_config().catalog_path)
    return conn


def test_no_plan_everything_is_loose_by_day(srv: Client) -> None:
    out = srv.call("GET", "/api/outline")[1]
    assert out["trips"] == [] and out["frames"] == 3
    assert out["items"] == [{"kind": "day", "key": "loose:2026-11-10", "day": "2026-11-10", "groups": 3}]


def test_accepted_proposed_and_loose(srv: Client, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _apply_one(srv)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(_plan(srv.ids)))
    monkeypatch.setenv("PHOTOVAULT_DEV_PLAN", str(plan))
    out = srv.call("GET", "/api/outline")[1]
    # the applied shoot is accepted, in its accepted trip; the proposal keeps only the group no shoot holds yet
    # (b and c are in the plan; both are unapplied)
    by_key = {t["key"]: t for t in out["trips"]}
    assert set(by_key) == {"t1", "t:1"}
    acc, prop = by_key["t1"], by_key["t:1"]
    assert (acc["state"], acc["name"], acc["groups"]) == ("accepted", "Dune trip", 1)
    assert (prop["state"], prop["name"], prop["groups"]) == ("proposed", "Coast week", 2)
    s_acc, s_prop = acc["shoots"][0], prop["shoots"][0]
    assert (s_acc["key"], s_acc["state"], s_acc["title"]) == ("s7", "accepted", "Dunes · Sunrise")
    assert s_acc["shoot_id"] == 7 and s_acc["proposal_id"] is None
    assert (s_prop["key"], s_prop["state"], s_prop["title"]) == ("s:1", "proposed", "Harbourton · Boats")
    assert s_prop["proposal_id"] == "s:1" and s_prop["sample_group_ids"] == [srv.ids["b"]]
    assert s_prop["days"] == [{"day": "2026-11-10", "groups": 2}]
    assert out["items"][0]["kind"] == "trip" and len(out["items"]) == 2  # no loose frames left
    # name shading (rv-47): accepted = 1; proposed = the lower of place and subject; a trip = its shoots, by photos
    assert (s_acc["confidence"], acc["confidence"]) == (1.0, 1.0)
    assert (s_prop["confidence"], prop["confidence"]) == (0.4, 0.4)


def test_loose_when_unplanned_and_key_resolution(srv: Client, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    plan = _plan(srv.ids)
    plan["shoots"][0]["group_ids"] = [srv.ids["b"]]
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(plan))
    monkeypatch.setenv("PHOTOVAULT_DEV_PLAN", str(p))
    out = srv.call("GET", "/api/outline")[1]
    kinds = [(i["kind"], i["groups"]) for i in out["items"]]
    assert sorted(kinds) == [("day", 2), ("trip", 1)]
    # shoot_key on /api/groups: a proposal, loose:<day>, and nonsense
    got = srv.call("GET", "/api/groups?shoot_key=s:1")[1]["groups"]
    assert [g["group_id"] for g in got] == [srv.ids["b"]]
    got = srv.call("GET", "/api/groups?shoot_key=loose:2026-11-10")[1]["groups"]
    assert sorted(g["group_id"] for g in got) == sorted([srv.ids["a"], srv.ids["c"]])
    assert srv.call("GET", "/api/groups?shoot_key=nope")[0] == 400


def test_drive_filter_counts_only_that_drive(srv: Client, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _apply_one(srv)
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_plan(srv.ids)))
    monkeypatch.setenv("PHOTOVAULT_DEV_PLAN", str(p))
    out = srv.call("GET", "/api/outline?volume_id=vol-leg")[1]
    # only group c lives on vol-leg: it is in the proposal; the accepted trip has no frames there
    assert out["frames"] == 1 and [t["key"] for t in out["trips"]] == ["t:1"]
    shoot = out["trips"][0]["shoots"][0]
    assert shoot["groups"] == 1 and shoot["files"] == 1 and shoot["bytes"] == 10
    assert out["other"] == {"files": 0, "bytes": 0}


def test_files_filter_by_shoot_key(srv: Client, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    _apply_one(srv)
    p = tmp_path / "plan.json"
    p.write_text(json.dumps(_plan(srv.ids)))
    monkeypatch.setenv("PHOTOVAULT_DEV_PLAN", str(p))
    got = srv.call("GET", "/api/volumes/vol-work/files?shoot_key=s7")[1]
    assert got["total"] == 1 and got["files"][0]["rel_path"] == "Photos/DSC_0001.JPG"
    got = srv.call("GET", "/api/volumes/vol-work/files?shoot_key=s:1")[1]
    assert got["total"] == 0  # group c's file is on the other drive
    got = srv.call("GET", "/api/volumes/vol-leg/files?shoot_key=s:1")[1]
    assert got["total"] == 1


def test_proposal_index_is_cached_per_plan(srv: Client, tmp_path) -> None:
    plan = _plan(srv.ids)
    a = outline.proposal_index(plan)
    assert outline.proposal_index(plan) is a
    assert a[srv.ids["b"]] == "s:1"
    assert outline.proposal_index(None) == {}


def test_other_files_have_no_photo_group(srv: Client) -> None:
    conn = db.connect(load_config().catalog_path)
    from photovault.catalog import repo
    repo.upsert_instance(conn, volume_id="vol-work", rel_path="Docs/notes.txt", size=5, mtime_ns=1, kind="other",
                         state="indexed", asset_id=None)
    conn.commit()
    conn.close()
    out = srv.call("GET", "/api/outline?volume_id=vol-work")[1]
    assert out["other"] == {"files": 1, "bytes": 5}
    got = srv.call("GET", "/api/volumes/vol-work/files?shoot_key=other")[1]
    assert [f["rel_path"] for f in got["files"]] == ["Docs/notes.txt"] and got["total"] == 1


def test_a_place_saved_on_photos_beats_the_plan_at_once(srv: Client, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    """D-147: the outline reads saved places live from the catalog, so a save shows before the next re-plan."""
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(_plan(srv.ids)))
    monkeypatch.setenv("PHOTOVAULT_DEV_PLAN", str(plan))
    conn = _conn()
    conn.execute("INSERT INTO group_meta (group_id, place_name, updated_at) VALUES (?, 'Point Example', 'x') "
                 "ON CONFLICT(group_id) DO UPDATE SET place_name = excluded.place_name", (srv.ids["b"],))
    conn.commit()
    conn.close()
    out = srv.call("GET", "/api/outline")[1]
    s = next(t for t in out["trips"] if t["key"] == "t:1")["shoots"][0]
    assert s["place"] == "Point Example" and s["title"] == "Point Example · Boats"
    assert s["confidence"] == 0.4  # the subject is still a guess; the place is certain
