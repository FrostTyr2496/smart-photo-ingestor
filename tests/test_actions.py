"""Actions engine (act-1, D-084): each rule on a synthetic catalog that triggers it and one that does not, the
ordering, stable ids, dismiss/snooze/restore through the API, and the cache. Names are invented. Never /Volumes."""

from __future__ import annotations

import itertools
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from photovault import actions, metrics
from photovault.catalog import db, repo
from tests.test_web_api import Client, srv  # noqa: F401  (the server fixture)

NOW = datetime(2026, 11, 18, 22, 0, tzinfo=UTC)
_seq = itertools.count(1)


@pytest.fixture
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "cat.sqlite")
    db.migrate(c)
    yield c
    c.close()


def vol(conn, vid: str, role: str, label: str, *, seen_days: int = 0) -> None:
    repo.upsert_volume(conn, volume_id=vid, role=role, label=label, fs_type="exfat", da_volume_uuid=f"U-{vid}",
                       marker_id=None, capacity_bytes=10**12, mount_path=f"/mnt/{label}")
    seen = (NOW - timedelta(days=seen_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    conn.execute("UPDATE volumes SET last_seen_at = ? WHERE volume_id = ?", (seen, vid))


def group(conn, **kw) -> int:
    n = next(_seq)
    args = dict(body_id=None, shutter_count=n, capture_time="2026-11-10T21:00:00", capture_offset=None,
                time_source="exif", canonical_stem=f"S{n}", original_stem=f"IMG_{n}", original_num=n, shoot_id=None)
    return repo.insert_group(conn, **(args | kw))


def asset(conn, group_id: int | None = None, size: int = 100) -> int:
    n = next(_seq)
    return repo.insert_asset(conn, sha256=f"{n:064x}", size=size, partial_sha256="p", kind="photo",
                             group_id=group_id, group_role="primary" if group_id else None)


def inst(conn, vid: str, asset_id: int | None, *, state: str = "verified", size: int = 100) -> None:
    repo.upsert_instance(conn, volume_id=vid, rel_path=f"DCIM/F{next(_seq)}.JPG", size=size, mtime_ns=1,
                         kind="photo", state=state, asset_id=asset_id)


def run(conn, mounted=(), home=None):
    return actions.compute(conn, now=NOW, mounted=mounted, home=home)


def kinds(found) -> set[str]:
    return {a["kind"] for a in found}


def only(found, kind: str) -> dict:
    hits = [a for a in found if a["kind"] == kind]
    assert len(hits) == 1, kinds(found)
    return hits[0]


def incident(store, key: str, path: str, ts: str = "2026-11-10T10:00:00") -> None:
    store.execute("INSERT INTO incidents (ts, volume_key, op, kind, errno, path, message) "
                  "VALUES (?, ?, 'read_full', 'io', 5, ?, 'EIO')", (ts, key, path))


# --------------------------------------------------------------------------- rule 1


def test_card_unbacked(conn) -> None:
    vol(conn, "card", "CARD", "Card A", seen_days=3)
    vol(conn, "work", "WORKING", "Small SSD")
    inst(conn, "card", asset(conn), size=500)
    a = only(run(conn), "card_unbacked")
    assert a["severity"] == "critical" and a["id"] == "card-unbacked:card"
    assert a["evidence"] == {"files": 1, "bytes": 500}
    assert "Card A" in a["title"] and "Small SSD" in " ".join(a["steps"])
    assert a["do"] == {"label": "Back up", "kind": "ui", "href": "#/cards/card"}
    assert a["needs"][0] == {"volume_id": "card", "label": "Card A", "role": "CARD", "mounted": False}
    assert 'ingest --source "/Volumes/Card A"' in a["cli"]  # a placeholder: not mounted
    assert "Last seen 3 days ago" in a["why"]


def test_card_unbacked_uses_the_real_mount_when_plugged_in(conn) -> None:
    vol(conn, "card", "CARD", "Card A")
    inst(conn, "card", asset(conn))
    a = only(run(conn, {"card": "/Volumes/NIKON Z 6_2"}), "card_unbacked")
    assert '--source "/Volumes/NIKON Z 6_2"' in a["cli"] and a["needs"][0]["mounted"] is True


def test_card_unbacked_names_the_recommended_pair(conn, tmp_path: Path, monkeypatch) -> None:
    from photovault import targets
    monkeypatch.setattr(targets, "is_solid_state", lambda mount, run=None: True)
    for name in ("w", "m"):
        (tmp_path / name).mkdir()
    vol(conn, "card", "CARD", "Card A")
    vol(conn, "w", "WORKING", "Travel SSD")
    vol(conn, "m", "MASTER", "Backup")
    inst(conn, "card", asset(conn), size=500)
    mounted = {"card": "/mnt/card", "w": str(tmp_path / "w"), "m": str(tmp_path / "m")}
    a = only(run(conn, mounted), "card_unbacked")
    assert any("Travel SSD" in st and "trailing copy onto Backup" in st for st in a["steps"])
    assert [n["label"] for n in a["needs"]] == ["Card A", "Travel SSD", "Backup"]
    assert any("card turns green" in w for w in a["why"])
    assert f'--target "{tmp_path / "w"}"' in a["cli"]


def test_single_copy_names_the_best_other_drive(conn, tmp_path: Path, monkeypatch) -> None:
    from photovault import targets
    monkeypatch.setattr(targets, "is_solid_state", lambda mount, run=None: True)
    for name in ("old", "new"):
        (tmp_path / name).mkdir()
    vol(conn, "old", "WORKING", "Old SSD")
    vol(conn, "new", "MASTER", "Big Backup")
    inst(conn, "old", asset(conn), size=100)
    a = only(run(conn, {"old": str(tmp_path / "old"), "new": str(tmp_path / "new")}), "single_copy")
    assert any("Copy Old SSD onto Big Backup" in st for st in a["steps"])
    assert a["cli"].startswith("uv run photovault backup --from-folder") and f'--to "{tmp_path / "new"}"' in a["cli"]
    assert only(run(conn), "single_copy")["cli"] is None          # nothing plugged in: no recommendation


def test_card_backed_up_is_quiet(conn) -> None:
    vol(conn, "card", "CARD", "Card A")
    vol(conn, "work", "WORKING", "SSD")
    a = asset(conn)
    inst(conn, "card", a)
    inst(conn, "work", a)
    assert "card_unbacked" not in kinds(run(conn))


def test_a_legacy_copy_is_not_a_backup(conn) -> None:
    vol(conn, "card", "CARD", "Card A")
    vol(conn, "leg", "LEGACY", "Old")
    a = asset(conn)
    inst(conn, "card", a)
    inst(conn, "leg", a)
    assert "card_unbacked" in kinds(run(conn))


# --------------------------------------------------------------------------- rule 2


def test_card_unindexed(conn) -> None:
    vol(conn, "fresh", "CARD", "Card B")
    vol(conn, "half", "CARD", "Card C")
    inst(conn, "half", None, state="indexed")
    found = [a for a in run(conn) if a["kind"] == "card_unindexed"]
    assert {a["id"] for a in found} == {"card-unindexed:fresh", "card-unindexed:half"}
    assert all(a["severity"] == "high" for a in found)


def test_card_unindexed_runs_the_check_when_mounted(conn) -> None:
    vol(conn, "half", "CARD", "Card C")
    inst(conn, "half", None, state="indexed")
    a = only(run(conn, {"half": "/Volumes/C"}), "card_unindexed")
    assert a["do"]["kind"] == "run" and a["do"]["body"] == {"cmd": "card_check", "args": {"mount": "/Volumes/C"}}


def test_card_unindexed_quiet_when_hashed(conn) -> None:
    vol(conn, "card", "CARD", "Card A")
    vol(conn, "work", "WORKING", "SSD")
    a = asset(conn)
    inst(conn, "card", a)
    inst(conn, "work", a)
    assert "card_unindexed" not in kinds(run(conn))


# --------------------------------------------------------------------------- rules 3, 4


def test_single_copy_and_escalation(conn, tmp_path: Path) -> None:
    vol(conn, "leg", "LEGACY", "Old Drive")
    vol(conn, "work", "WORKING", "SSD")
    inst(conn, "leg", asset(conn), size=2000)
    two = asset(conn)
    inst(conn, "leg", two)
    inst(conn, "work", two)
    a = only(run(conn), "single_copy")
    assert a["id"] == "single-copy:leg" and a["severity"] == "high"
    assert a["evidence"] == {"files": 1, "bytes": 2000}
    store = metrics.open_store(tmp_path)
    incident(store, "leg", "/mnt/Old Drive/Trips/x/a.NEF")
    store.close()
    assert only(run(conn, home=tmp_path), "single_copy")["severity"] == "critical"


def test_single_copy_ignores_cards_and_second_copies(conn) -> None:
    vol(conn, "card", "CARD", "Card A")
    vol(conn, "a", "WORKING", "A")
    vol(conn, "b", "MASTER", "B")
    inst(conn, "card", asset(conn))
    both = asset(conn)
    inst(conn, "a", both)
    inst(conn, "b", both)
    assert "single_copy" not in kinds(run(conn))


def test_drive_errors(conn, tmp_path: Path) -> None:
    vol(conn, "leg", "LEGACY", "Old Drive")
    assert run(conn, home=tmp_path) == []  # no metrics db: quietly nothing
    store = metrics.open_store(tmp_path)
    for n in range(3):
        incident(store, "leg", f"/mnt/Old Drive/Trips/Hills/img{n}.NEF")
    incident(store, "other", "/x/y", "2025-01-01T10:00:00")
    store.close()
    a = only(run(conn, home=tmp_path), "drive_errors")
    assert a["severity"] == "high" and a["evidence"]["folder"] == "Trips/Hills" and a["evidence"]["files"] == 3
    assert '--skip-prefix "Trips/Hills"' in a["cli"]


def test_drive_errors_ignores_old_incidents(conn, tmp_path: Path) -> None:
    vol(conn, "leg", "LEGACY", "Old Drive")
    store = metrics.open_store(tmp_path)
    incident(store, "leg", "/mnt/Old Drive/a/b.NEF", "2025-01-01T10:00:00")
    store.close()
    assert "drive_errors" not in kinds(run(conn, home=tmp_path))


# --------------------------------------------------------------------------- rules 5..8


def test_job_paused(conn) -> None:
    vol(conn, "card", "CARD", "Card A")
    vol(conn, "work", "WORKING", "SSD")
    job = repo.create_job(conn, kind="card_ingest", source_volume_id="card", target_volume_id="work", params={})
    repo.add_job_items(conn, job, [{"seq": 1, "src_rel": "a", "dst_rel": "a", "group_id": None, "role": None},
                                   {"seq": 2, "src_rel": "b", "dst_rel": "b", "group_id": None, "role": None}])
    repo.set_job_state(conn, job, "paused")
    a = only(run(conn), "job_paused")
    assert a["severity"] == "normal" and a["id"] == f"job-paused:{job}"
    assert a["do"]["body"] == {"cmd": "resume", "args": {"job_id": job}}
    assert "0 of 2 files done" in a["title"]


def test_job_paused_quiet_when_done_or_superseded(conn) -> None:
    vol(conn, "d", "WORKING", "SSD")
    done = repo.create_job(conn, kind="index_volume", source_volume_id="d", target_volume_id=None, params={})
    repo.set_job_state(conn, done, "done")
    assert "job_paused" not in kinds(run(conn))
    old = repo.create_job(conn, kind="baseline_hash", source_volume_id="d", target_volume_id=None, params={})
    repo.set_job_state(conn, old, "paused")
    assert "job_paused" in kinds(run(conn))
    new = repo.create_job(conn, kind="baseline_hash", source_volume_id="d", target_volume_id=None, params={})
    repo.set_job_state(conn, new, "done")
    assert "job_paused" not in kinds(run(conn))


def test_hash_incomplete(conn) -> None:
    vol(conn, "leg", "LEGACY", "Old Drive")
    inst(conn, "leg", None, state="indexed", size=300)
    inst(conn, "leg", asset(conn))
    a = only(run(conn), "hash_incomplete")
    assert a["severity"] == "normal" and a["evidence"] == {"files": 1, "bytes": 300}
    assert "1 of 2" in a["why"][0]
    mounted = only(run(conn, {"leg": "/Volumes/Old"}), "hash_incomplete")
    assert mounted["do"]["body"] == {"cmd": "hash", "args": {"mount": "/Volumes/Old"}}


def test_hash_incomplete_quiet_when_hashed_or_paused(conn) -> None:
    vol(conn, "leg", "LEGACY", "Old Drive")
    inst(conn, "leg", asset(conn))
    assert "hash_incomplete" not in kinds(run(conn))
    inst(conn, "leg", None, state="indexed")
    job = repo.create_job(conn, kind="baseline_hash", source_volume_id="leg", target_volume_id=None, params={})
    repo.set_job_state(conn, job, "paused")
    assert "hash_incomplete" not in kinds(run(conn))  # the paused job is the action


def test_drive_stale(conn) -> None:
    vol(conn, "leg", "LEGACY", "Old Drive", seen_days=60)
    inst(conn, "leg", asset(conn))
    a = only(run(conn), "drive_stale")
    assert a["severity"] == "normal" and "60 days" in a["title"]
    assert "drive_stale" not in kinds(run(conn, {"leg": "/Volumes/Old"}))  # plugged in now


def test_drive_stale_quiet_when_recent_or_duplicated(conn) -> None:
    vol(conn, "leg", "LEGACY", "Old Drive", seen_days=5)
    vol(conn, "m", "MASTER", "Master", seen_days=90)
    inst(conn, "leg", asset(conn))
    both = asset(conn)
    inst(conn, "m", both)
    inst(conn, "leg", both)
    assert "drive_stale" not in kinds(run(conn))


def test_sidecars_dirty(conn) -> None:
    vol(conn, "work", "WORKING", "SSD")
    vol(conn, "leg", "LEGACY", "Old")
    g, h = group(conn), group(conn)
    inst(conn, "work", asset(conn, g))
    inst(conn, "leg", asset(conn, h))
    for gid in (g, h):
        conn.execute("INSERT INTO group_meta (group_id, dirty, updated_at) VALUES (?, 1, 'x')", (gid,))
    a = only(run(conn, {"work": "/Volumes/SSD"}), "sidecars_dirty")  # only the WORKING drive's file counts
    assert a["evidence"]["files"] == 1 and a["do"]["body"] == {"cmd": "meta_sidecars", "args": {"mount": "/Volumes/SSD"}}


def test_sidecars_clean_is_quiet(conn) -> None:
    vol(conn, "work", "WORKING", "SSD")
    g = group(conn)
    inst(conn, "work", asset(conn, g))
    conn.execute("INSERT INTO group_meta (group_id, dirty, updated_at) VALUES (?, 0, 'x')", (g,))
    assert "sidecars_dirty" not in kinds(run(conn))


# --------------------------------------------------------------------------- rules 9..12


def test_metadata_stale(conn) -> None:
    vol(conn, "leg", "LEGACY", "Old Drive")
    g = group(conn)
    inst(conn, "leg", asset(conn, g))
    a = only(run(conn), "metadata_stale")
    assert a["severity"] == "low" and a["evidence"]["files"] == 1
    conn.execute("INSERT INTO group_meta (group_id, updated_at) VALUES (?, 'x')", (g,))
    conn.execute("UPDATE capture_groups SET subsec = '12'")
    assert "metadata_stale" not in kinds(run(conn))


def test_locations_suggested(conn) -> None:
    vol(conn, "leg", "LEGACY", "Old Drive")
    near = group(conn, capture_time="2026-11-10T21:00:00", capture_offset="+01:00")
    far = group(conn, capture_time="2026-11-12T09:00:00")
    for g in (near, far):
        inst(conn, "leg", asset(conn, g))
    assert "locations_suggested" not in kinds(run(conn))  # no GPS fixes yet
    conn.execute("INSERT INTO geo_points (utc_time, lat, lon, source, source_ref) "
                 "VALUES ('2026-11-10T20:10:00+00:00', 1.0, 2.0, 'phone', 'p1')")  # 20:00 UTC + 10 min
    a = only(run(conn), "locations_suggested")
    assert a["severity"] == "low" and a["evidence"]["files"] == 1 and a["evidence"]["top_day"] == "2026-11-10"
    assert a["do"]["href"] == "#/photos?missing=gps&day=2026-11-10"
    conn.execute("INSERT INTO group_meta (group_id, lat, lon, updated_at) VALUES (?, 1, 2, 'x')", (near,))
    assert "locations_suggested" not in kinds(run(conn))  # it already has one


def test_shoots_review(conn, tmp_path: Path) -> None:
    import json
    group(conn)
    group(conn)
    a = only(run(conn, home=tmp_path), "shoots_review")  # no plan yet: offer to make one
    assert a["do"]["kind"] == "run" and a["do"]["body"] == {"cmd": "shoots_plan", "args": {}}
    assert a["cli"] == "uv run photovault --json shoots plan" and a["evidence"]["files"] == 2
    (tmp_path / "plans").mkdir()
    plan = {"shoots": [{"proposal_id": "a", "locked": False}, {"proposal_id": "b", "locked": False},
                       {"proposal_id": "c", "locked": True}]}
    (tmp_path / "plans" / "shoots-1.json").write_text(json.dumps(plan))
    a = only(run(conn, home=tmp_path), "shoots_review")
    assert a["do"]["href"] == "#/review/shoots" and a["evidence"]["files"] == 2 and a["evidence"]["undecided"] == 2
    conn.execute("INSERT INTO shoot_plan_decisions (proposal_id, kind, payload_json, decided_at) "
                 "VALUES ('a', 'accept', '{}', 'x')")
    assert only(run(conn, home=tmp_path), "shoots_review")["evidence"]["undecided"] == 1
    (tmp_path / "plans" / "shoots-2.json").write_text("not json")  # the newest file wins; unreadable counts as none
    assert only(run(conn, home=tmp_path), "shoots_review")["evidence"]["undecided"] == 0
    conn.execute("INSERT INTO shoots (name, shoot_date, created_at) VALUES ('S', '2026-11-10', 'x')")
    conn.execute("UPDATE capture_groups SET shoot_id = 1")
    assert "shoots_review" not in kinds(run(conn, home=tmp_path))


def test_clock_suggested(conn, monkeypatch) -> None:
    from photovault import clock
    assert "clock_suggested" not in kinds(run(conn))
    sug = {"body_id": 3, "body": "Z6", "start_utc": "2026-04-02T08:00:00+00:00", "end_utc": "2026-04-07T18:00:00+00:00",
           "first_date": "2026-04-02", "last_date": "2026-04-07", "frames": 40, "delta_s": 28800,
           "sessions_matched": 5, "sessions_total": 6}
    monkeypatch.setattr(clock, "suggest_offsets", lambda c: [sug])
    a = only(run(conn), "clock_suggested")
    assert a["id"] == "clock:3:2026-04-02T08:00:00+00:00" and a["severity"] == "low"
    assert a["title"] == "Align Z6 for 2\u20137 Apr: 40 photos" and a["do"]["href"] == "#/photos"
    assert "8 hours behind" in a["why"][0]


def test_duplicates_reclaimable(conn, tmp_path: Path) -> None:
    assert run(conn, home=tmp_path) == []
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "duplicates-20261117-100000.md").write_text(
        "## Summary\n\n- Duplicate clusters: 12 (30 files)\n- Reclaimable: 1.0 GB (1000000000 bytes)\n")
    a = only(run(conn, home=tmp_path), "duplicates_reclaimable")
    assert a["severity"] == "low" and a["evidence"]["bytes"] == 10**9 and a["do"]["kind"] == "none"
    (tmp_path / "reports" / "duplicates-20261118-100000.md").write_text(
        "- Duplicate clusters: 0 (0 files)\n- Reclaimable: 0 B (0 bytes)\n")
    assert "duplicates_reclaimable" not in kinds(run(conn, home=tmp_path))  # the newest report wins


# --------------------------------------------------------------------------- ordering, ids, state


def test_ordering_and_stable_ids(conn) -> None:
    vol(conn, "c1", "CARD", "Small card", seen_days=1)
    vol(conn, "c2", "CARD", "Big card", seen_days=9)
    vol(conn, "leg", "LEGACY", "Old Drive", seen_days=1)
    inst(conn, "c1", asset(conn), size=10)
    inst(conn, "c2", asset(conn), size=9000)
    inst(conn, "leg", None, state="indexed")
    first = run(conn)
    sev = [a["severity"] for a in first]
    assert sev == sorted(sev, key=actions.SEVERITIES.index)
    crit = [a["id"] for a in first if a["severity"] == "critical"]
    assert crit == ["card-unbacked:c2", "card-unbacked:c1"]  # more bytes at risk first
    assert [a["id"] for a in first] == [a["id"] for a in run(conn)]  # stable
    assert all(not k.startswith("_") for a in actions.build_response(first, {}, NOW)["actions"] for k in a)


def test_state_dismiss_snooze_and_expiry(conn) -> None:
    vol(conn, "c1", "CARD", "Card A")
    vol(conn, "c2", "CARD", "Card B")
    inst(conn, "c1", asset(conn))
    inst(conn, "c2", asset(conn))
    found = run(conn)
    state = {"card-unbacked:c1": {"dismissed": True},
             "card-unbacked:c2": {"dismissed": False, "until": (NOW + timedelta(days=2)).isoformat()}}
    out = actions.build_response(found, state, NOW)
    assert out["counts"]["critical"] == 0 and out["counts"]["dismissed"] == 1 and out["counts"]["snoozed"] == 1
    by = {a["id"]: a for a in out["actions"]}  # hidden ones stay in the list, flagged
    assert by["card-unbacked:c1"]["dismissed"] is True and by["card-unbacked:c1"]["snoozed_until"] is None
    assert by["card-unbacked:c2"]["dismissed"] is False and by["card-unbacked:c2"]["snoozed_until"]
    out = actions.build_response(found, state, NOW + timedelta(days=3))  # the snooze ran out
    assert out["counts"]["snoozed"] == 0 and out["counts"]["critical"] == 1


# --------------------------------------------------------------------------- the API


def seed_card(client) -> None:
    conn = db.connect(client.app.catalog_path)
    vol(conn, "card-x", "CARD", "Card X")
    inst(conn, "card-x", asset(conn))
    conn.close()


def find(body: dict, id_: str) -> dict:
    return next(a for a in body["actions"] if a["id"] == id_)


def test_api_contract_and_round_trip(srv: Client) -> None:  # noqa: F811
    seed_card(srv)
    status, body = srv.call("GET", "/api/actions")
    assert status == 200 and set(body) == {"generated_at", "counts", "actions"}
    assert set(body["counts"]) == {"critical", "high", "normal", "low", "snoozed", "dismissed"}
    card = find(body, "card-unbacked:card-x")
    assert set(card) == {"id", "kind", "severity", "title", "why", "needs", "steps", "do", "cli", "evidence",
                         "snoozed_until", "dismissed"}
    crit = body["counts"]["critical"]
    assert crit >= 1 and card["dismissed"] is False

    assert srv.call("POST", "/api/actions/card-unbacked%3Acard-x/snooze", {"days": 7})[0] == 200
    _, body = srv.call("GET", "/api/actions")
    assert find(body, "card-unbacked:card-x")["snoozed_until"]
    assert body["counts"]["snoozed"] == 1 and body["counts"]["critical"] == crit - 1

    assert srv.call("POST", "/api/actions/card-unbacked:card-x/restore")[0] == 200
    _, body = srv.call("GET", "/api/actions")
    assert body["counts"]["snoozed"] == 0 and body["counts"]["critical"] == crit
    assert find(body, "card-unbacked:card-x")["snoozed_until"] is None

    assert srv.call("POST", "/api/actions/card-unbacked:card-x/dismiss")[0] == 200
    _, body = srv.call("GET", "/api/actions")
    assert body["counts"]["dismissed"] == 1 and find(body, "card-unbacked:card-x")["dismissed"] is True
    assert srv.call("POST", "/api/actions/card-unbacked:card-x/restore")[0] == 200
    assert srv.call("POST", "/api/actions/no-such:thing/dismiss")[0] == 404
    assert srv.call("POST", "/api/actions/card-unbacked:card-x/snooze", {"days": 0})[0] == 400
    assert srv.call("GET", "/api/actions/card-unbacked:card-x/dismiss")[0] == 405


def test_api_cache_drops_on_a_mount_change(srv: Client) -> None:  # noqa: F811
    seed_card(srv)
    _, body = srv.call("GET", "/api/actions")
    assert find(body, "card-unbacked:card-x")["needs"][0]["mounted"] is False
    conn = db.connect(srv.app.catalog_path)  # plug the card in: its disk UUID matches the registered volume
    conn.execute("UPDATE volumes SET da_volume_uuid = 'UUID-CARDX' WHERE volume_id = 'card-x'")
    conn.close()
    (srv.root / "CARDX" / "DCIM").mkdir(parents=True)
    _, body = srv.call("GET", "/api/actions")  # inside the 30 s window, yet the mounted set changed
    assert find(body, "card-unbacked:card-x")["needs"][0]["mounted"] is True
