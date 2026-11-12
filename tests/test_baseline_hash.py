"""Baseline full hash (idx-2) on tmp_path "legacy drives". Never touches /Volumes; trees are never modified."""

from __future__ import annotations

import hashlib
import io
import os
import shutil
from pathlib import Path

import pytest

import photovault.index as index_mod
from photovault import metadata, volumes
from photovault.catalog import db, repo
from photovault.config import load_config
from photovault.index import baseline_hash, throttle_delay
from photovault.protocol import Emitter
from tests.test_cli import fake_probe, fake_read, result, run, tree_hashes


def make_drive(root: Path) -> Path:
    for folder in ("Photos/2024/A", "Photos/2025/B"):
        d = root / folder
        d.mkdir(parents=True)
        for i in (1, 2):
            (d / f"DSC_{i:04d}.NEF").write_bytes(os.urandom(5000 + i))
            (d / f"DSC_{i:04d}.JPG").write_bytes(os.urandom(800 + i))
    return root


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", fake_read)
    return {"vols": tmp_path / "Volumes", "drive": make_drive(tmp_path / "Volumes" / "OLD1")}


def assign_and_index(capsys, drive: Path, label: str = "OLD") -> None:
    code, lines = run(capsys, "volumes", "assign", str(drive), "--role", "LEGACY", "--label", label)
    assert code == 0, lines
    code, lines = run(capsys, "index", str(drive))
    assert code == 0, lines


def conn():
    c = db.connect(load_config().catalog_path)
    db.migrate(c)
    return c


def test_hash_links_every_instance(env, capsys) -> None:
    before = tree_hashes(env["drive"])
    assign_and_index(capsys, env["drive"])
    code, lines = run(capsys, "hash", str(env["drive"]))
    assert code == 0, lines
    r = result(lines)
    assert r["hashed"] == 8 and r["skipped"] == 0
    c = conn()
    rows = c.execute("SELECT i.*, a.sha256, a.group_id FROM instances i JOIN assets a USING (asset_id)").fetchall()
    assert len(rows) == 8
    for row in rows:
        assert row["state"] == "verified" and row["verified_sha256"] == row["sha256"]
        assert row["sha256"] == hashlib.sha256((env["drive"] / row["rel_path"]).read_bytes()).hexdigest()
        assert row["group_id"] is not None  # linked through the index job's group items
    code, lines = run(capsys, "hash", str(env["drive"]))
    assert result(lines)["hashed"] == 0  # nothing left to do
    assert tree_hashes(env["drive"]) == before


def test_identical_files_on_two_volumes_share_one_asset(env, capsys) -> None:
    other = env["vols"] / "OLD2"
    shutil.copytree(env["drive"], other)
    assign_and_index(capsys, env["drive"], "OLD")
    assign_and_index(capsys, other, "OLD2")
    run(capsys, "hash", str(env["drive"]))
    run(capsys, "hash", str(other))
    c = conn()
    assert c.execute("SELECT COUNT(*) FROM assets").fetchone()[0] == 8
    counts = [r[0] for r in c.execute("SELECT COUNT(*) FROM instances GROUP BY asset_id")]
    assert counts == [2] * 8


def test_stop_then_resume_same_job(env, capsys) -> None:
    assign_and_index(capsys, env["drive"])
    c = conn()
    cfg = load_config()
    calls = {"n": 0}

    def stop_after_3() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    job_id = baseline_hash(c, cfg, env["drive"], emit=Emitter(io.StringIO()), should_stop=stop_after_3)
    assert repo.get_job(c, job_id)["state"] == "paused"
    assert c.execute("SELECT COUNT(*) FROM instances WHERE state = 'verified'").fetchone()[0] == 3
    job2 = baseline_hash(c, cfg, env["drive"], emit=Emitter(io.StringIO()))
    assert job2 == job_id and repo.get_job(c, job_id)["state"] == "done"
    assert c.execute("SELECT COUNT(*) FROM instances WHERE state = 'verified'").fetchone()[0] == 8


def test_file_changed_since_index_is_skipped(env, capsys) -> None:
    assign_and_index(capsys, env["drive"])
    (env["drive"] / "Photos/2024/A/DSC_0001.JPG").write_bytes(b"different now")
    code, lines = run(capsys, "hash", str(env["drive"]))
    r = result(lines)
    assert r["hashed"] == 7 and r["skipped"] == 1
    assert any(e.get("code") == "skipped" and "index again" in e["message"] for e in lines)


def test_throttle_maths() -> None:
    assert throttle_delay(10_000_000, 0.5, None) == 0.0
    assert throttle_delay(10_000_000, 0.5, 10) == pytest.approx(0.5)  # 10 MB at 10 MB/s needs 1 s
    assert throttle_delay(10_000_000, 2.0, 10) == 0.0  # already slower than the cap


def test_hash_sleeps_to_honour_max_mbps(env, capsys, monkeypatch) -> None:
    assign_and_index(capsys, env["drive"])
    sleeps: list[float] = []
    monkeypatch.setattr(index_mod.time, "sleep", sleeps.append)
    monkeypatch.setattr(index_mod.time, "monotonic", lambda: 100.0)  # hashing appears instant
    c = conn()
    baseline_hash(c, load_config(), env["drive"], emit=Emitter(io.StringIO()), max_mbps=0.001)  # 1,000 B/s
    sizes = [r["size"] for r in c.execute("SELECT size FROM instances ORDER BY rel_path")]
    expected, done = [], 0
    for s in sizes:
        done += s
        expected.append(done / 1000)
    assert sleeps == pytest.approx(expected)


def test_card_frame_known_only_on_legacy_is_still_imported(env, capsys) -> None:
    """D-036: a verified LEGACY copy does not meet the WORKING policy, so the card file is copied."""
    card = env["vols"] / "CARD1"
    d = card / "DCIM" / "100NZ6_2"
    d.mkdir(parents=True)
    shutil.copy2(env["drive"] / "Photos/2024/A/DSC_0001.NEF", d / "DSC_0001.NEF")
    assign_and_index(capsys, env["drive"])
    run(capsys, "hash", str(env["drive"]))
    work = env["vols"] / "WORK"
    work.mkdir()
    code, _ = run(capsys, "volumes", "assign", str(work), "--role", "WORKING")
    assert code == 0
    code, lines = run(capsys, "ingest", "--source", str(card), "--target", str(work))
    assert code == 0 and result(lines)["copied"] == 1 and result(lines)["skipped_dup"] == 0
    code, _ = run(capsys, "card-status", str(card))
    assert code == 0
    c = conn()
    assert c.execute("SELECT COUNT(*) FROM instances WHERE asset_id = (SELECT asset_id FROM instances "
                     "WHERE rel_path = 'Photos/2024/A/DSC_0001.NEF')").fetchone()[0] == 3  # legacy, card, working
