"""Copy-target recommendations (rec-1, D-103): ranking, exclusions with reasons, the proposed pair, the endpoint.
Invented drives; `diskutil` and free space are faked. Never touches /Volumes."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

from photovault import metrics, targets
from photovault.catalog import db, repo
from tests.test_web_api import Client, srv  # noqa: F401  (the server fixture)

GB = 10**9


@pytest.fixture
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "cat.sqlite")
    db.migrate(c)
    targets.clear_cache()
    yield c
    c.close()


def vol(conn, vid: str, role: str, label: str, fs: str = "exfat") -> None:
    repo.upsert_volume(conn, volume_id=vid, role=role, label=label, fs_type=fs, da_volume_uuid=f"U-{vid}",
                       marker_id=None, capacity_bytes=10**12, mount_path=f"/mnt/{label}")


def fake_run(solid: dict[str, bool]):
    def run(argv, **_):  # noqa: ANN001
        mount = argv[-1]
        if mount not in solid:
            return subprocess.CompletedProcess(argv, 1, b"", b"nope")
        return subprocess.CompletedProcess(argv, 0, plistlib.dumps({"SolidState": solid[mount]}), b"")
    return run


def mounts(*vids: str) -> dict[str, str]:
    return {v: f"/mnt/{v}" for v in vids}


@pytest.fixture
def free(monkeypatch):
    table: dict[str, int | None] = {}
    monkeypatch.setattr(targets, "_free_bytes", lambda mount: table.get(mount, 500 * GB))
    return table


def by_id(rec: dict) -> dict[str, dict]:
    return {c["volume_id"]: c for c in rec["candidates"]}


def test_ntfs_and_unmounted_drives_are_excluded_with_a_reason(conn, free) -> None:
    vol(conn, "card", "CARD", "Card A")
    vol(conn, "w", "WORKING", "Fast SSD")
    vol(conn, "n", "WORKING", "Old NTFS", "ufsd_ntfs")
    vol(conn, "gone", "WORKING", "Unplugged")
    vol(conn, "leg", "LEGACY", "Legacy")
    rec = targets.recommend(conn, None, mounts("w", "n", "leg", "card"), planned_bytes=GB, source_volume_id="card",
                            run=fake_run({}))
    cands = by_id(rec)
    assert set(cands) == {"w", "n"}                       # not LEGACY, not a card, not unplugged
    assert cands["n"]["usable"] is False and "NTFS: not written to" in cands["n"]["reasons"][0]
    assert cands["w"]["usable"] and cands["w"]["rank"] == 1 and cands["n"]["rank"] == 2
    assert rec["pair"]["read"] == "w" and rec["pair"]["copy"] is None and "one drive" in rec["pair"]["note"]


def test_free_space_needs_the_plan_plus_five_percent(conn, free) -> None:
    vol(conn, "w1", "WORKING", "Tight")
    vol(conn, "w2", "WORKING", "Roomy")
    free["/mnt/w1"] = 100 * GB + 1 * GB      # 101 GB free
    rec = targets.recommend(conn, None, mounts("w1", "w2"), planned_bytes=100 * GB, source_volume_id=None, run=fake_run({}))
    cands = by_id(rec)
    assert cands["w1"]["usable"] is False and "free" in cands["w1"]["reasons"][0] and "needs" in cands["w1"]["reasons"][0]
    free["/mnt/w1"] = 106 * GB
    assert by_id(targets.recommend(conn, None, mounts("w1", "w2"), planned_bytes=100 * GB, source_volume_id=None,
                                   run=fake_run({})))["w1"]["usable"] is True


def test_ssd_beats_hdd_and_measured_speed_orders_the_rest(conn, free, tmp_path: Path) -> None:
    vol(conn, "hdd", "WORKING", "Spinning")
    vol(conn, "ssd1", "WORKING", "SSD slow")
    vol(conn, "ssd2", "WORKING", "SSD fast")
    store = metrics.open_store(tmp_path)
    for vid, mbps in (("ssd1", 100), ("ssd2", 400), ("hdd", 900)):
        store.execute("INSERT INTO daily (day, volume_key, op, source, files, bytes, busy_s, errors) "
                      "VALUES ('2026-11-10', ?, 'write', 'live', 1, ?, 10, 0)", (vid, mbps * 10 * 10**6))
    store.commit()
    store.close()
    run = fake_run({"/mnt/hdd": False, "/mnt/ssd1": True, "/mnt/ssd2": True})
    rec = targets.recommend(conn, tmp_path, mounts("hdd", "ssd1", "ssd2"), planned_bytes=GB, source_volume_id=None, run=run)
    assert [c["volume_id"] for c in rec["candidates"]] == ["ssd2", "ssd1", "hdd"]
    cands = by_id(rec)
    assert "SSD" in cands["ssd2"]["reasons"] and "400 MB/s write measured" in cands["ssd2"]["reasons"]
    assert any("Spinning hard disk" in r and "journal" in r for r in cands["hdd"]["reasons"])
    assert rec["pair"] == {"read": "ssd2", "copy": "ssd1", "note": "Import onto SSD fast, then copy each file onto SSD slow: "
                           "two drives, so the card turns green."}


def test_read_errors_demote_a_drive_and_say_so(conn, free, tmp_path: Path) -> None:
    vol(conn, "bad", "WORKING", "Flaky")
    vol(conn, "ok", "WORKING", "Steady")
    store = metrics.open_store(tmp_path)
    store.execute("INSERT INTO daily (day, volume_key, op, source, files, bytes, busy_s, errors) "
                  "VALUES ('2026-11-10', 'bad', 'read_full', 'live', 1, 1000, 1, 3)")
    store.commit()
    store.close()
    run = fake_run({"/mnt/bad": True, "/mnt/ok": True})
    rec = targets.recommend(conn, tmp_path, mounts("bad", "ok"), planned_bytes=GB, source_volume_id=None, run=run)
    assert [c["volume_id"] for c in rec["candidates"]] == ["ok", "bad"]
    assert "3 read errors on record" in by_id(rec)["bad"]["reasons"]


def test_pair_puts_the_card_read_on_a_working_drive_and_the_copy_on_the_master(conn, free) -> None:
    vol(conn, "card", "CARD", "Card A")
    vol(conn, "w", "WORKING", "Travel SSD")
    vol(conn, "m", "MASTER", "Backup", "apfs")
    rec = targets.recommend(conn, None, mounts("w", "m"), planned_bytes=GB, source_volume_id="card", run=fake_run({}))
    assert rec["pair"]["read"] == "w" and rec["pair"]["copy"] == "m"
    assert any("turns the card green" in r for r in by_id(rec)["m"]["reasons"])
    only_master = targets.recommend(conn, None, mounts("m"), planned_bytes=GB, source_volume_id="card", run=fake_run({}))
    assert only_master["pair"]["read"] is None and "No connected working drive" in only_master["pair"]["note"]


def test_existing_copies_are_named(conn, free) -> None:
    vol(conn, "card", "CARD", "Card A")
    vol(conn, "w", "WORKING", "Travel SSD")
    a = repo.insert_asset(conn, sha256="a" * 64, size=10, partial_sha256="p", kind="raw")
    repo.upsert_instance(conn, volume_id="card", rel_path="DCIM/A.NEF", size=10, mtime_ns=1, kind="raw", state="indexed", asset_id=a)
    repo.upsert_instance(conn, volume_id="w", rel_path="x/A.NEF", size=10, mtime_ns=1, kind="raw", state="verified", asset_id=a)
    rec = targets.recommend(conn, None, mounts("w"), planned_bytes=10, source_volume_id="card", run=fake_run({}))
    assert "Already holds 1 of this card's files" in by_id(rec)["w"]["reasons"]
    assert targets.card_unbacked_bytes(conn, "card") == 0


def test_exclude_leaves_the_holding_drive_out(conn, free) -> None:
    vol(conn, "a", "WORKING", "A")
    vol(conn, "b", "WORKING", "B")
    rec = targets.recommend(conn, None, mounts("a", "b"), planned_bytes=GB, source_volume_id=None, run=fake_run({}),
                            exclude={"a"})
    assert set(by_id(rec)) == {"b"}


def test_diskutil_failure_means_unknown_not_an_error(conn, free) -> None:
    vol(conn, "w", "WORKING", "Mystery")
    rec = targets.recommend(conn, None, mounts("w"), planned_bytes=GB, source_volume_id=None, run=fake_run({}))
    assert rec["candidates"][0]["solid_state"] is None and rec["candidates"][0]["usable"]


def test_endpoint(srv: Client, monkeypatch) -> None:  # noqa: F811
    from tests.test_web_api import seed_card
    monkeypatch.setattr(targets, "is_solid_state", lambda mount, run=None: True)
    assert srv.call("GET", "/api/cards/nope/targets")[0] == 404
    seed_card(srv)
    status, payload = srv.call("GET", "/api/cards/vol-card/targets")
    assert status == 200 and payload["planned_bytes"] == 70       # only N.NEF has no WORKING copy yet
    work = [c for c in payload["candidates"] if c["label"] == "WORK" or c["volume_id"] == "vol-work"]
    assert work and work[0]["usable"] and work[0]["solid_state"] is True and work[0]["reasons"]
    assert payload["pair"]["read"] == work[0]["volume_id"]
    assert srv.call("GET", "/api/cards/vol-card/targets?planned_bytes=5")[1]["planned_bytes"] == 5
    assert srv.call("POST", "/api/cards/vol-card/targets", {})[0] == 405
