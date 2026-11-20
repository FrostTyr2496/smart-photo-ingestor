"""sh-8: membership candidates, scores and details on synthetic catalogs. Names and places are invented."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from photovault import shoots
from photovault.catalog import repo
from tests.test_shoots import Builder, conn, d  # noqa: F401  (fixtures)


@pytest.fixture
def b(conn):  # noqa: F811
    return Builder(conn)


def put_analysis(conn, start_utc, count, step_s=600, *, labels=(), hints=None, caption=None, persons=(), city=None,
                 place=None, lat=1.0, lon=1.0):
    """Phone points with a phone_analysis row each."""
    for k in range(count):
        t = start_utc + timedelta(seconds=k * step_s)
        cur = conn.execute(
            "INSERT INTO geo_points (utc_time, lat, lon, source, source_ref, local_offset, place_name, city) "
            "VALUES (?,?,?,?,?,?,?,?)", (t.isoformat(), lat, lon, "iphone", f"{t.isoformat()}-{caption}-{labels}",
                                         "+00:00", place, city))
        conn.execute("INSERT INTO phone_analysis (point_id, caption, labels_json, persons_json, hints_json) "
                     "VALUES (?,?,?,?,?)", (cur.lastrowid, caption, json.dumps(list(labels)),
                                            json.dumps(list(persons)), json.dumps(hints or {})))


def test_astro_night_with_phone_frames_8h_before(conn, b):
    b.frames(d(9, 22), 30, lat=1.0, lon=1.0)  # 22:00 - 22:29
    put_analysis(conn, datetime(2023, 9, 9, 13, 30, tzinfo=UTC), 3, labels=["milky way", "sky"], city="Harbourton")
    det = shoots.plan(conn)["shoots"][0]["details"]
    assert det["place"]["value"] == "Harbourton" and det["place"]["source"] == "phone_area"
    assert 0 < det["place"]["confidence"] < 0.8
    assert det["subject"]["value"] is None
    alts = det["subject"]["alternatives"]
    assert [a["value"] for a in alts] == ["milky way"] and alts[0]["beyond_cutoff"] and 0 <= alts[0]["confidence"] < 1
    repo.set_setting(conn, "shoot_review_beta", "false")
    det = shoots.plan(conn)["shoots"][0]["details"]
    assert det["subject"]["value"] is None and det["subject"]["alternatives"] == []
    assert det["place"]["value"] == "Harbourton"


def test_subject_from_phone_inside_the_core_and_stoplist(conn, b):
    b.frames(d(9, 22), 30)
    put_analysis(conn, datetime(2023, 9, 9, 22, 0, tzinfo=UTC), 4, labels=["Sky", "Stargazing"],
                 hints={"activities": ["Camping"], "venues": ["Dune Lookout"]}, caption="Stars over the dunes",
                 persons=["Ann Example"])
    det = shoots.plan(conn)["shoots"][0]["details"]
    assert det["subject"]["value"] in ("Stargazing", "Camping") and det["subject"]["confidence"] > 0.7
    assert "Sky" not in det["subject"]["top_terms"] and len(det["subject"]["top_terms"]) == 2
    assert det["subject"]["example_caption"]["value"] == "Stars over the dunes"
    assert det["place"]["value"] == "Dune Lookout" and det["place"]["source"] == "phone_venue"
    assert det["people"]["value"] == ["Ann Example"]
    json.dumps(det)


def test_candidates_and_core_sessions(conn, b):
    a = b.frames(d(9, 20), 10)
    c = b.frames(d(9, 22), 10)  # about 1 h 51 min after the last frame: a split with gap_h=1
    s0, s1 = shoots.plan(conn, gap_h=1)["shoots"]
    assert s0["members"]["core_sessions"] == [[a[0], a[-1]]]
    cand = s0["candidates"][0]
    assert cand["session"] == [c[0], c[-1]] and cand["home_proposal"] == s1["proposal_id"]
    assert cand["band"] in ("likely", "maybe") and cand["frames"] == 10
    assert 1 <= len(cand["reasons"]) <= 3 and all({"term", "text"} <= set(r) for r in cand["reasons"])
    assert any("after" in r["text"] for r in cand["reasons"])


def test_second_body_with_unaligned_clock_scores_lower(conn, b):
    b.frames(d(9, 20), 10, body="A")
    b.frames(d(9, 22), 10, body="A")
    b.frames(d(9, 23, 30), 10, body="B", off=None)  # a second body whose clock is assumed
    first = shoots.plan(conn, gap_h=1)["shoots"][0]
    by_body = {tuple(c["bodies"]): c for c in first["candidates"]}
    assert by_body[("A",)]["score"] > by_body[("B",)]["score"]
    assert any(r["term"] == "clock" for r in by_body[("B",)]["reasons"])


def test_a_sequence_across_the_boundary_is_offered(conn, b):
    b.frames(d(9, 20), 10)
    ids = b.frames(d(9, 22, 30), 10)
    conn.execute("INSERT INTO sequences (sequence_id, kind) VALUES (5, 'timelapse')")
    conn.execute("UPDATE capture_groups SET sequence_id = 5 WHERE group_id IN (10, ?)", (ids[0],))
    pid = shoots.plan(conn)["shoots"][0]["proposal_id"]  # one shoot: a sequence is never split by the planner
    with repo.transaction(conn):  # a user split inside the sequence
        shoots.add_decisions(conn, [{"proposal_id": pid, "kind": "split", "payload": {"after_group_id": 10}}])
    cand = shoots.plan(conn)["shoots"][0]["candidates"][0]
    assert any(r["text"] == "same sequence" for r in cand["reasons"])


def test_far_candidates_stay_in_beta_and_go_without_it(conn, b):
    b.frames(d(9, 8), 10)
    b.frames(d(9, 13), 10, lat=50.0, lon=50.0)  # 4 h later, far away
    b.frames(d(9, 8), 1, lat=1.0, lon=1.0)
    first = shoots.plan(conn, gap_h=1)
    low = [c for s in first["shoots"] for c in s["candidates"] if c["band"] == "beyond_cutoff"]
    repo.set_setting(conn, "shoot_review_beta", "false")
    second = shoots.plan(conn, gap_h=1)
    assert all(c["band"] != "beyond_cutoff" for s in second["shoots"] for c in s["candidates"])
    total = lambda p: sum(len(s["candidates"]) for s in p["shoots"])  # noqa: E731
    assert total(first) == total(second) + len(low)


def test_exclude_and_include_replay(conn, b):
    a = b.frames(d(9, 20), 10)
    c = b.frames(d(9, 22), 10)
    s0, _ = shoots.plan(conn, gap_h=1)["shoots"]
    with repo.transaction(conn):
        shoots.add_decisions(conn, [{"proposal_id": s0["proposal_id"], "kind": "include",
                                     "payload": {"session": [c[0], c[-1]]}}])
    p = shoots.plan(conn, gap_h=1)
    assert len(p["shoots"]) == 1 and p["shoots"][0]["proposal_id"] == s0["proposal_id"]
    assert p["shoots"][0]["counts"]["frames"] == 20 and len(p["shoots"][0]["members"]["core_sessions"]) == 2
    with repo.transaction(conn):
        shoots.add_decisions(conn, [{"proposal_id": s0["proposal_id"], "kind": "exclude",
                                     "payload": {"session": [c[0], c[-1]]}}])
    p = shoots.plan(conn, gap_h=1)
    assert p["shoots"][0]["group_ids"] == a
    assert p["unassigned_sessions"][0]["session"] == [c[0], c[-1]]
    assert all(cd["session"] != [c[0], c[-1]] for cd in p["shoots"][0]["candidates"])  # never offered again


def test_detail_decision_sets_value_with_user_source(conn, b):
    b.frames(d(9, 22), 10)
    pid = shoots.plan(conn)["shoots"][0]["proposal_id"]
    with repo.transaction(conn):
        shoots.add_decisions(conn, [
            {"proposal_id": pid, "kind": "detail", "payload": {"field": "subject", "value": "Dune astro"}},
            {"proposal_id": pid, "kind": "detail", "payload": {"field": "place", "value": "Dune Lookout"}}])
    det = shoots.plan(conn)["shoots"][0]["details"]
    assert det["subject"]["value"] == "Dune astro" and det["subject"]["source"] == "user"
    assert det["subject"]["confidence"] == 1.0 and det["place"]["value"] == "Dune Lookout"


@pytest.mark.parametrize("bad", [
    {"proposal_id": "s:abcdef12", "kind": "include", "payload": {}},
    {"proposal_id": "s:abcdef12", "kind": "exclude", "payload": {"session": [1]}},
    {"proposal_id": "s:abcdef12", "kind": "detail", "payload": {"field": "colour", "value": "x"}},
    {"proposal_id": "s:abcdef12", "kind": "detail", "payload": {"field": "people", "value": "x"}},
])
def test_new_decision_kinds_are_checked(bad):
    with pytest.raises(ValueError):
        shoots.check_decision(bad)
