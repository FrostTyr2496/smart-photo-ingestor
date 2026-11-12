"""Index a volume (idx-1) on a tmp_path "legacy drive". Never touches /Volumes; the tree is never modified."""

from __future__ import annotations

import io
import os
import sqlite3
from pathlib import Path

import pytest

from photovault import metadata, volumes
from photovault.catalog import db, repo
from photovault.config import load_config
from photovault.index import index_volume
from photovault.protocol import Emitter
from tests.test_cli import fake_probe, fake_read, result, run, tree_hashes


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", fake_read)
    drive = tmp_path / "Volumes" / "LEGACY1"
    for folder in ("Photos/2024/Trip A", "Photos/2025/Trip B"):
        d = drive / folder
        d.mkdir(parents=True)
        for i in (1, 2):
            (d / f"DSC_{i:04d}.NEF").write_bytes(os.urandom(5000 + i))
            (d / f"DSC_{i:04d}.JPG").write_bytes(os.urandom(800 + i))
        (d / "DSC_0001.xmp").write_text("<x/>")
    (drive / "Catalog.lrcat").write_bytes(b"not to be opened")
    (drive / "notes.txt").write_text("ignored")
    return {"drive": drive}


def assign(capsys, drive: Path) -> None:
    code, lines = run(capsys, "volumes", "assign", str(drive), "--role", "LEGACY", "--label", "OLD")
    assert code == 0, lines
    assert not (drive / ".photovault-id").exists()  # no marker on LEGACY (vault-rules 1)


def conn() -> sqlite3.Connection:
    c = db.connect(load_config().catalog_path)
    db.migrate(c)
    return c


def states(c: sqlite3.Connection) -> dict[str, str]:
    return {r["rel_path"]: r["state"] for r in c.execute("SELECT rel_path, state FROM instances")}


def test_index_tree_then_reindex_is_noop(env, capsys) -> None:
    before = tree_hashes(env["drive"])
    assign(capsys, env["drive"])
    code, lines = run(capsys, "index", str(env["drive"]))
    assert code == 0, lines
    r = result(lines)
    assert r["files"] == 10 and r["indexed"] == 10 and r["unchanged"] == 0 and r["missing"] == 0
    c = conn()
    s = states(c)
    assert len(s) == 10 and set(s.values()) == {"indexed"}
    assert "Catalog.lrcat" not in s and "notes.txt" not in s
    assert c.execute("SELECT COUNT(*) FROM instances WHERE partial_sha256 IS NULL").fetchone()[0] == 0
    groups = c.execute("SELECT canonical_stem, original_stem FROM capture_groups").fetchall()
    assert len(groups) == 2  # same fake serial+shutter in both folders: one group per (body, shutter)
    assert all(g["canonical_stem"] == g["original_stem"] for g in groups)
    job = c.execute("SELECT kind, state FROM jobs").fetchone()
    assert (job["kind"], job["state"]) == ("index_volume", "done")

    code, lines = run(capsys, "index", str(env["drive"]))
    r = result(lines)
    assert code == 0 and r["indexed"] == 0 and r["unchanged"] == 10
    assert tree_hashes(env["drive"]) == before


def test_deleted_file_becomes_missing_and_modified_is_reindexed(env, capsys) -> None:
    assign(capsys, env["drive"])
    run(capsys, "index", str(env["drive"]))
    c = conn()
    # Pretend the baseline hash had linked one file to an asset.
    with repo.transaction(c):
        aid = repo.insert_asset(c, sha256="a" * 64, size=1, partial_sha256="p", kind="raw")
        c.execute("UPDATE instances SET asset_id = ?, state = 'verified', verified_sha256 = ? "
                  "WHERE rel_path = 'Photos/2024/Trip A/DSC_0002.NEF'", (aid, "a" * 64))

    (env["drive"] / "Photos/2025/Trip B/DSC_0001.JPG").unlink()
    changed = env["drive"] / "Photos/2024/Trip A/DSC_0002.NEF"
    changed.write_bytes(os.urandom(7000))
    code, lines = run(capsys, "index", str(env["drive"]))
    r = result(lines)
    assert code == 0 and r["indexed"] == 1 and r["missing"] == 1 and r["unchanged"] == 8
    s = states(c)
    assert s["Photos/2025/Trip B/DSC_0001.JPG"] == "missing"
    row = c.execute("SELECT * FROM instances WHERE rel_path = 'Photos/2024/Trip A/DSC_0002.NEF'").fetchone()
    assert row["state"] == "indexed" and row["asset_id"] is None and row["verified_sha256"] is None
    assert row["size"] == 7000

    # It comes back: indexed again (identity is re-established by the baseline hash).
    (env["drive"] / "Photos/2025/Trip B/DSC_0001.JPG").write_bytes(b"back")
    code, lines = run(capsys, "index", str(env["drive"]))
    assert result(lines)["indexed"] == 1 and states(c)["Photos/2025/Trip B/DSC_0001.JPG"] == "indexed"


def test_stop_pauses_then_rerun_resumes_same_job(env, capsys, monkeypatch) -> None:
    import photovault.index as index_mod

    monkeypatch.setattr(index_mod, "BATCH", 1)
    assign(capsys, env["drive"])
    c = conn()
    cfg = load_config()
    calls = {"n": 0}

    def stop_after_first_dir() -> bool:
        calls["n"] += 1
        return calls["n"] > 1

    out = io.StringIO()
    job_id = index_volume(c, cfg, env["drive"], emit=Emitter(out), should_stop=stop_after_first_dir)
    assert repo.get_job(c, job_id)["state"] == "paused"
    first = len(states(c))
    assert 0 < first < 10
    assert c.execute("SELECT COUNT(*) FROM instances WHERE state = 'missing'").fetchone()[0] == 0

    job2 = index_volume(c, cfg, env["drive"], emit=Emitter(io.StringIO()))
    assert job2 == job_id and repo.get_job(c, job_id)["state"] == "done"
    assert len(states(c)) == 10 and set(states(c).values()) == {"indexed"}


def test_unregistered_volume_is_refused(env, capsys) -> None:
    code, lines = run(capsys, "index", str(env["drive"]))
    assert code == 1 and "volumes assign" in lines[-1]["message"]
