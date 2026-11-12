"""Ingest fault suite (ing-10): crashes, a vanished target, a flipped byte, re-runs and collisions.

Synthetic 10-file "card" in tmp_path with monkeypatched probe and metadata. Every scenario also checks that the
source tree's bytes and mtimes are unchanged (vault-rules 1). Never touches /Volumes.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
from pathlib import Path

import pytest

import photovault.copy_engine as copy_engine
import photovault.ingest as ingest_mod
from photovault import metadata, volumes
from photovault.config import load_config
from tests.test_cli import fake_probe, fake_read, media_files, result, run, tree_hashes

N = 10


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", fake_read)
    card = tmp_path / "Volumes" / "CARD1"
    d = card / "DCIM" / "100NZ6_2"
    d.mkdir(parents=True)
    for i in range(1, N + 1):
        (d / f"DSC_{i:04d}.NEF").write_bytes(os.urandom(200_000 + i))
    work = tmp_path / "Volumes" / "WORK"
    work.mkdir()
    before = tree_hashes(card)
    yield {"card": card, "work": work}
    assert tree_hashes(card) == before, "the source card changed"


def assign(capsys, env) -> str:
    code, lines = run(capsys, "volumes", "assign", str(env["work"]), "--role", "WORKING", "--label", "WORK")
    assert code == 0, lines
    return volumes.read_marker(env["work"])


def ingest(capsys, env, *extra: str) -> tuple[int, list[dict]]:
    return run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]), *extra)


def catalog() -> sqlite3.Connection:
    conn = sqlite3.connect(load_config().catalog_path)
    conn.row_factory = sqlite3.Row
    return conn


def working_verified() -> int:
    with catalog() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM instances i JOIN volumes v USING (volume_id) "
            "WHERE v.role = 'WORKING' AND i.state = 'verified'"
        ).fetchone()[0]


def job_state(job_id: int = 1) -> str:
    with catalog() as conn:
        return conn.execute("SELECT state FROM jobs WHERE job_id = ?", (job_id,)).fetchone()[0]


def partials(root: Path) -> list[Path]:
    return list(root.rglob("*.pvpartial"))


def assert_complete(capsys, env) -> None:
    assert len(media_files(env["work"])) == N
    assert partials(env["work"]) == []
    assert working_verified() == N
    code, lines = run(capsys, "card-status", str(env["card"]))
    assert code == 0 and result(lines)["state"] == "green"


def test_exception_mid_job_pauses_then_resume_completes(env, capsys, monkeypatch) -> None:
    assign(capsys, env)
    real = ingest_mod.copy_verified
    calls = {"n": 0}

    def boom(src, dst, **kw):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 5:
            raise RuntimeError("simulated engine bug")
        return real(src, dst, **kw)

    monkeypatch.setattr(ingest_mod, "copy_verified", boom)
    code, lines = ingest(capsys, env)
    assert code == 1 and "simulated engine bug" in lines[-1]["message"]
    assert job_state() == "paused"
    with catalog() as conn:
        states = dict(conn.execute("SELECT state, COUNT(*) FROM job_items GROUP BY state").fetchall())
    assert states == {"committed": 4, "planned": 6}
    assert len(media_files(env["work"])) == 4

    monkeypatch.setattr(ingest_mod, "copy_verified", real)
    code, lines = run(capsys, "resume", "1")
    assert code == 0 and result(lines)["copied"] == 6
    assert job_state() == "done"
    assert_complete(capsys, env)


def test_hard_crash_leaves_partial_which_resume_sweeps(env, capsys, monkeypatch) -> None:
    assign(capsys, env)
    real = ingest_mod.copy_verified
    calls = {"n": 0}

    def crash(src, dst, **kw):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 5:  # like a kill mid-write: a partial is left behind, nothing cleans it up
            dst.parent.mkdir(parents=True, exist_ok=True)
            copy_engine.partial_path(dst).write_bytes(b"half a file")
            raise KeyboardInterrupt
        return real(src, dst, **kw)

    monkeypatch.setattr(ingest_mod, "copy_verified", crash)
    with pytest.raises(KeyboardInterrupt):
        ingest(capsys, env)
    assert job_state() == "paused"
    assert len(partials(env["work"])) == 1
    # A stale partial beside an already committed file (from an earlier crash) is swept too.
    first = media_files(env["work"])[0]
    copy_engine.partial_path(first).write_bytes(b"stale")

    monkeypatch.setattr(ingest_mod, "copy_verified", real)
    code, lines = run(capsys, "resume", "1")
    assert code == 0 and result(lines)["copied"] == 6
    assert any(e.get("code") == "partials_swept" for e in lines)
    assert_complete(capsys, env)


def test_target_removed_mid_job_pauses_then_resume_recopies(env, capsys, monkeypatch) -> None:
    marker = assign(capsys, env)
    real = ingest_mod.copy_verified
    calls = {"n": 0}

    def vanish(src, dst, **kw):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 4:
            shutil.rmtree(env["work"])
            raise OSError("No such file or directory")
        return real(src, dst, **kw)

    monkeypatch.setattr(ingest_mod, "copy_verified", vanish)
    code, _ = ingest(capsys, env)
    assert code == 4 and job_state() == "paused"

    env["work"].mkdir()
    volumes.write_marker(env["work"], marker)
    monkeypatch.setattr(ingest_mod, "copy_verified", real)
    code, lines = run(capsys, "resume", "1")
    assert code == 0, lines
    # The 3 files committed before the removal were gone: they are re-queued, not trusted.
    assert result(lines)["copied"] == N
    assert any(e.get("code") == "target_missing" for e in lines)
    assert_complete(capsys, env)


def test_flipped_byte_on_readback_fails_only_that_item(env, capsys, monkeypatch) -> None:
    assign(capsys, env)
    real_hash = copy_engine._hash_uncached

    def flip(path: Path) -> str:
        if path.name.endswith(".pvpartial") and "_0001003." in path.name:
            data = bytearray(path.read_bytes())
            data[1000] ^= 0xFF
            path.write_bytes(bytes(data))
        return real_hash(path)

    monkeypatch.setattr(copy_engine, "_hash_uncached", flip)
    code, lines = ingest(capsys, env)
    assert code == 1
    r = result(lines)
    assert r["copied"] == N - 1 and r["failed"] == 1
    failed = [e for e in lines if e["event"] == "item" and e["state"] == "failed"]
    assert len(failed) == 1 and "verify mismatch" in failed[0]["error"]
    assert not (env["work"] / failed[0]["dst"]).exists()
    assert partials(env["work"]) == []
    assert job_state() == "failed"
    code, lines = run(capsys, "card-status", str(env["card"]))
    assert code == 3 and result(lines)["unknown"] == 1

    monkeypatch.setattr(copy_engine, "_hash_uncached", real_hash)
    code, lines = run(capsys, "resume", "1")
    assert code == 0 and result(lines)["copied"] == 1
    assert any(e.get("code") == "retry_failed" for e in lines)
    assert_complete(capsys, env)


def test_second_run_copies_nothing(env, capsys) -> None:
    assign(capsys, env)
    code, lines = ingest(capsys, env)
    assert code == 0 and result(lines)["copied"] == N
    code, lines = ingest(capsys, env)
    assert code == 0
    assert result(lines)["copied"] == 0 and result(lines)["skipped_dup"] == N
    assert_complete(capsys, env)


def test_name_collision_leaves_existing_file_untouched(env, capsys) -> None:
    assign(capsys, env)
    code, lines = ingest(capsys, env, "--dry-run")
    first_dst = result(lines)["sample"][0].split(" -> ")[1]
    existing = env["work"] / first_dst
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"a different file")
    before = existing.stat().st_mtime_ns

    code, lines = ingest(capsys, env)
    assert code == 1
    failed = [e for e in lines if e["event"] == "item" and e["state"] == "failed"]
    assert len(failed) == 1 and "name collision" in failed[0]["error"]
    assert existing.read_bytes() == b"a different file" and existing.stat().st_mtime_ns == before
    assert working_verified() == N - 1
    code, _ = run(capsys, "card-status", str(env["card"]))
    assert code == 3

    # A resume retries the collision, and it fails the same way: still never overwritten.
    code, lines = run(capsys, "resume", "1")
    assert code == 1 and result(lines)["failed"] == 1
    assert existing.read_bytes() == b"a different file"


def test_sweep_partials_only_touches_temp_names(tmp_path: Path) -> None:
    dst = tmp_path / "a" / "X.NEF"
    dst.parent.mkdir()
    dst.write_bytes(b"final")
    copy_engine.partial_path(dst).write_bytes(b"tmp")
    assert copy_engine.sweep_partials([dst, tmp_path / "a" / "Y.NEF"]) == 1
    assert dst.read_bytes() == b"final" and not copy_engine.partial_path(dst).exists()
