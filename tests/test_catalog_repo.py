"""fnd-3: catalog repository."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from photovault.catalog import db, repo


@pytest.fixture
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "catalog.sqlite")
    db.migrate(c)
    yield c
    c.close()


def _vol(conn, volume_id="v1", role="WORKING", **kw):
    args = dict(volume_id=volume_id, role=role, label="L", fs_type="exfat", da_volume_uuid=None,
                marker_id=None, capacity_bytes=1000, mount_path="/mnt/x")
    args.update(kw)
    repo.upsert_volume(conn, **args)


def _asset(conn, sha="a" * 64, **kw):
    args = dict(sha256=sha, size=10, partial_sha256="p" * 64, kind="raw")
    args.update(kw)
    return repo.insert_asset(conn, **args)


def test_utcnow_format() -> None:
    assert repo.utcnow().endswith("Z") and len(repo.utcnow()) == 20


def test_path_key_nfc_and_casefold() -> None:
    assert repo.path_key("DCIM/A.NEF") == repo.path_key("dcim/a.nef")
    assert repo.path_key("café.nef") == repo.path_key("café.NEF")


def test_upsert_volume_insert_then_update_keeps_created_at(conn) -> None:
    _vol(conn, marker_id="m1")
    first = conn.execute("SELECT * FROM volumes").fetchone()
    _vol(conn, marker_id="m1", label="Renamed", mount_path="/mnt/y")
    rows = conn.execute("SELECT * FROM volumes").fetchall()
    assert len(rows) == 1
    assert rows[0]["label"] == "Renamed" and rows[0]["last_mount_path"] == "/mnt/y"
    assert rows[0]["created_at"] == first["created_at"]


def test_find_volume_by_marker_then_uuid(conn) -> None:
    _vol(conn, "v1", marker_id="m1")
    _vol(conn, "v2", role="LEGACY", da_volume_uuid="U2")
    assert repo.find_volume(conn, marker_id="m1")["volume_id"] == "v1"
    assert repo.find_volume(conn, da_volume_uuid="U2")["volume_id"] == "v2"
    assert repo.find_volume(conn, marker_id="nope", da_volume_uuid="U2")["volume_id"] == "v2"
    assert repo.find_volume(conn, marker_id="nope") is None
    assert repo.find_volume(conn) is None


def test_get_or_create_body(conn) -> None:
    a = repo.get_or_create_body(conn, "Z6II", "123", "Z6")
    assert repo.get_or_create_body(conn, "Z6II", "123", "other name") == a
    assert repo.get_or_create_body(conn, "Z6II", None, "x") != a
    assert conn.execute("SELECT nickname FROM bodies WHERE body_id=?", (a,)).fetchone()[0] == "Z6"


def test_find_asset_and_insert_asset_idempotent(conn) -> None:
    assert repo.find_asset(conn, "a" * 64) is None
    first = _asset(conn)
    assert _asset(conn, size=999) == first
    row = repo.find_asset(conn, "a" * 64)
    assert row["asset_id"] == first and row["size"] == 10


def test_find_candidates(conn) -> None:
    a = _asset(conn, "a" * 64)
    _asset(conn, "b" * 64, size=11)
    assert [r["asset_id"] for r in repo.find_candidates(conn, 10, "p" * 64)] == [a]
    assert repo.find_candidates(conn, 10, "q" * 64) == []


def test_upsert_instance_case_insensitive_unique(conn) -> None:
    _vol(conn)
    i1 = repo.upsert_instance(conn, volume_id="v1", rel_path="DCIM/A.NEF", size=1, mtime_ns=1, kind="raw",
                              state="indexed")
    i2 = repo.upsert_instance(conn, volume_id="v1", rel_path="dcim/a.nef", size=2, mtime_ns=2, kind="raw",
                              state="indexed")
    assert i1 == i2
    assert conn.execute("SELECT COUNT(*) FROM instances").fetchone()[0] == 1
    assert repo.get_instance(conn, "v1", "Dcim/A.nef")["size"] == 2
    assert repo.get_instance(conn, "v1", "other.nef") is None


def test_upsert_instance_verified_sets_time_and_keeps_asset(conn) -> None:
    _vol(conn)
    aid = _asset(conn)
    repo.upsert_instance(conn, volume_id="v1", rel_path="a.nef", size=1, mtime_ns=1, kind="raw",
                         state="indexed")
    assert repo.get_instance(conn, "v1", "a.nef")["verified_at"] is None
    repo.upsert_instance(conn, volume_id="v1", rel_path="a.nef", size=1, mtime_ns=1, kind="raw",
                         state="verified", asset_id=aid, verified_sha256="a" * 64)
    row = repo.get_instance(conn, "v1", "a.nef")
    assert row["verified_at"] is not None and row["asset_id"] == aid
    repo.upsert_instance(conn, volume_id="v1", rel_path="a.nef", size=1, mtime_ns=1, kind="raw",
                         state="missing")
    row = repo.get_instance(conn, "v1", "a.nef")
    assert row["state"] == "missing" and row["asset_id"] == aid and row["verified_at"] is not None


def test_verified_counts_by_role_excludes_card(conn) -> None:
    _vol(conn, "w", "WORKING")
    _vol(conn, "m", "MASTER")
    _vol(conn, "c", "CARD")
    aid = _asset(conn)
    for vid, rel, state in [("w", "a.nef", "verified"), ("w", "b.nef", "verified"), ("m", "a.nef", "indexed"),
                            ("c", "a.nef", "verified")]:
        repo.upsert_instance(conn, volume_id=vid, rel_path=rel, size=1, mtime_ns=1, kind="raw", state=state,
                             asset_id=aid)
    assert repo.verified_counts_by_role(conn, aid) == {"WORKING": 2}


def test_job_lifecycle(conn) -> None:
    _vol(conn, "c", "CARD")
    _vol(conn, "w", "WORKING")
    jid = repo.create_job(conn, kind="card_ingest", source_volume_id="c", target_volume_id="w",
                          params={"b": 1, "a": 2})
    job = conn.execute("SELECT * FROM jobs WHERE job_id=?", (jid,)).fetchone()
    assert job["state"] == "planned" and json.loads(job["params_json"]) == {"a": 2, "b": 1}

    repo.add_job_items(conn, jid, [
        {"seq": 2, "src_rel": "DCIM/b.nef", "dst_rel": "x/b.nef", "group_id": None, "role": "primary"},
        {"seq": 1, "src_rel": "DCIM/a.nef", "dst_rel": "x/a.nef", "group_id": None, "role": "primary"},
    ])
    assert [r["seq"] for r in repo.pending_items(conn, jid)] == [1, 2]

    aid = _asset(conn)
    repo.set_item_state(conn, jid, 1, "committed", asset_id=aid)
    repo.set_item_state(conn, jid, 2, "copying")
    assert [r["seq"] for r in repo.pending_items(conn, jid)] == [2]
    repo.set_item_state(conn, jid, 2, "failed", error="boom")
    assert repo.pending_items(conn, jid) == []
    item = conn.execute("SELECT * FROM job_items WHERE job_id=? AND seq=1", (jid,)).fetchone()
    assert item["asset_id"] == aid and item["error"] is None
    with pytest.raises(KeyError):
        repo.set_item_state(conn, jid, 99, "failed")

    repo.set_job_state(conn, jid, "running")
    assert conn.execute("SELECT finished_at FROM jobs").fetchone()[0] is None
    repo.set_job_state(conn, jid, "done")
    assert conn.execute("SELECT finished_at FROM jobs").fetchone()[0] is not None
    with pytest.raises(KeyError):
        repo.set_job_state(conn, 99, "done")


def test_transaction_commits_and_rolls_back(conn) -> None:
    with pytest.raises(RuntimeError):
        with repo.transaction(conn):
            _vol(conn)
            raise RuntimeError("abort")
    assert conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0] == 0
    with repo.transaction(conn):
        _vol(conn)
    assert conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0] == 1
    assert not conn.in_transaction
