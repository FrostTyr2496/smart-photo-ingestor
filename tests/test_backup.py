"""Backup job (bk-3, D-050, D-101): a card followed onto a second drive, a folder from a LEGACY drive, and the
faults. Synthetic drives in tmp_path with the probe and metadata patched. Never touches /Volumes."""

from __future__ import annotations

import io
import os
import sqlite3
from pathlib import Path

import pytest

import photovault.backup as backup_mod
from photovault import cards, ingest as ingest_mod, metadata, volumes
from photovault.catalog import db
from photovault.config import load_config
from photovault.protocol import Emitter
from tests.test_cli import fake_probe, fake_read, media_files, result, run, tree_hashes

N = 6


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", fake_read)
    vols = tmp_path / "Volumes"
    card = vols / "CARD1"
    d = card / "DCIM" / "100NZ6_2"
    d.mkdir(parents=True)
    for i in range(1, N + 1):
        (d / f"DSC_{i:04d}.NEF").write_bytes(os.urandom(100_000 + i))
    work, back, leg = vols / "WORK", vols / "BACK", vols / "LEG"
    for p in (work, back, leg):
        p.mkdir()
    shoot = leg / "Eclipse" / "Day 1"
    shoot.mkdir(parents=True)
    for i in range(1, 4):
        (shoot / f"E{i}.NEF").write_bytes(os.urandom(50_000 + i))
    (shoot / "notes.txt").write_text("not media")
    before = (tree_hashes(card), tree_hashes(leg))
    yield {"card": card, "work": work, "back": back, "leg": leg}
    assert (tree_hashes(card), tree_hashes(leg)) == before, "a source changed"


def catalog() -> sqlite3.Connection:
    conn = sqlite3.connect(load_config().catalog_path)
    conn.row_factory = sqlite3.Row
    return conn


def setup(capsys, env, *, import_card: bool = True) -> str:
    for key, role, label in (("work", "WORKING", "WORK"), ("back", "MASTER", "BACK"), ("leg", "LEGACY", "LEG")):
        code, lines = run(capsys, "volumes", "assign", str(env[key]), "--role", role, "--label", label)
        assert code == 0, lines
    if import_card:
        code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]))
        assert code == 0, lines
    with catalog() as conn:
        row = conn.execute("SELECT volume_id FROM volumes WHERE role = 'CARD'").fetchone()
    return row["volume_id"] if row else ""


def card_tiers(env) -> set[str]:
    with catalog() as conn:
        vid = conn.execute("SELECT volume_id FROM volumes WHERE role = 'CARD'").fetchone()[0]
        return {f["tier"] for f in cards.card_files(conn, vid, mount=env["card"])}


def backup(capsys, env, *extra: str, to: str = "back"):
    return run(capsys, "backup", "--to", str(env[to]), *extra)


def item_states(job_id: int) -> dict[str, int]:
    with catalog() as conn:
        return dict(conn.execute("SELECT state, COUNT(*) FROM job_items WHERE job_id = ? GROUP BY state",
                                 (job_id,)).fetchall())


def partials(root: Path) -> list[Path]:
    return list(root.rglob("*.pvpartial"))


# --------------------------------------------------------------------------- card source


def test_card_followed_onto_a_second_drive_turns_green(env, capsys) -> None:
    card_id = setup(capsys, env)
    assert card_tiers(env) == {"needs_backup"}                  # one drive: yellow
    code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]), "--follow", "1")
    assert code == 0, lines
    assert result(lines)["copied"] == N
    assert card_tiers(env) == {"backed_up"}                     # D-089: on a MASTER
    names = lambda root: sorted(p.relative_to(root) for p in media_files(root))  # noqa: E731
    assert names(env["back"]) == names(env["work"]) and len(names(env["back"])) == N
    assert partials(env["back"]) == []


def test_card_backup_without_follow_and_dry_run(env, capsys) -> None:
    card_id = setup(capsys, env)
    code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]), "--dry-run")
    assert code == 0
    plan = result(lines)
    assert plan["files"] == N and plan["target"] == "BACK" and plan["target_free_bytes"] is not None
    assert media_files(env["back"]) == []
    with catalog() as conn:
        assert conn.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'replicate'").fetchone()[0] == 0
    code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]))
    assert code == 0 and result(lines)["copied"] == N
    code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]))   # nothing left to copy
    assert code == 0 and result(lines)["copied"] == 0


def test_trailing_copies_each_file_as_the_import_commits(env, capsys) -> None:
    for key, role, label in (("work", "WORKING", "WORK"), ("back", "MASTER", "BACK")):
        assert run(capsys, "volumes", "assign", str(env[key]), "--role", role, "--label", label)[0] == 0
    cfg = load_config()
    conn = db.connect(cfg.catalog_path)
    db.migrate(conn)
    out = Emitter(io.StringIO(), json_mode=True)
    conn.execute("BEGIN IMMEDIATE")
    ing = ingest_mod.plan_card_ingest(conn, cfg, source=env["card"], target=env["work"], shoot_name=None, emit=out)
    conn.execute("COMMIT")
    card_id = conn.execute("SELECT volume_id FROM volumes WHERE role = 'CARD'").fetchone()[0]
    conn.execute("BEGIN IMMEDIATE")
    bk = backup_mod.plan_backup(conn, cfg, source=env["work"], target=env["back"], card_volume_id=card_id,
                                follow_job=ing, emit=out)
    conn.execute("COMMIT")
    polls: list[int] = []

    def poll(_: float) -> None:      # each poll lets the import commit a few more files
        polls.append(1)
        left = iter(range(3))
        ingest_mod.run_job(conn, cfg, ing, emit=out, should_stop=lambda: next(left, None) is None)
        if conn.execute("SELECT COUNT(*) FROM job_items WHERE job_id = ? AND state = 'planned'", (ing,)).fetchone()[0]:
            conn.execute("UPDATE jobs SET state = 'running' WHERE job_id = ?", (ing,))   # still importing

    summary = backup_mod.run_backup(conn, cfg, bk, out, lambda: False, sleep=poll, poll_s=0)
    assert not summary.paused and summary.copied == N and len(polls) >= 1
    assert conn.execute("SELECT state FROM jobs WHERE job_id = ?", (bk,)).fetchone()[0] == "done"


def test_upstream_paused_copies_what_is_committed_then_pauses(env, capsys) -> None:
    for key, role, label in (("work", "WORKING", "WORK"), ("back", "MASTER", "BACK")):
        assert run(capsys, "volumes", "assign", str(env[key]), "--role", role, "--label", label)[0] == 0
    cfg = load_config()
    conn = db.connect(cfg.catalog_path)
    db.migrate(conn)
    buf = io.StringIO()
    out = Emitter(buf, json_mode=True)
    conn.execute("BEGIN IMMEDIATE")
    ing = ingest_mod.plan_card_ingest(conn, cfg, source=env["card"], target=env["work"], shoot_name=None, emit=out)
    conn.execute("COMMIT")
    card_id = conn.execute("SELECT volume_id FROM volumes WHERE role = 'CARD'").fetchone()[0]
    conn.execute("BEGIN IMMEDIATE")
    bk = backup_mod.plan_backup(conn, cfg, source=env["work"], target=env["back"], card_volume_id=card_id,
                                follow_job=ing, emit=out)
    conn.execute("COMMIT")
    calls = {"n": 0}

    def stop_after_3() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    assert ingest_mod.run_job(conn, cfg, ing, emit=out, should_stop=stop_after_3).paused
    summary = backup_mod.run_backup(conn, cfg, bk, out, lambda: False, poll_s=0)
    assert summary.paused and summary.copied == 3
    assert '"code": "upstream_paused"' in buf.getvalue()
    assert conn.execute("SELECT state FROM jobs WHERE job_id = ?", (bk,)).fetchone()[0] == "paused"
    ingest_mod.run_job(conn, cfg, ing, emit=out)                   # the import resumes and finishes
    summary = backup_mod.run_backup(conn, cfg, bk, out, lambda: False, poll_s=0)
    assert not summary.paused and summary.copied == N - 3


# --------------------------------------------------------------------------- folder source


def test_folder_backup_from_a_legacy_drive(env, capsys) -> None:
    setup(capsys, env, import_card=False)
    folder = env["leg"] / "Eclipse"
    code, lines = run(capsys, "backup", "--from-folder", str(folder), "--to", str(env["back"]), "--dry-run")
    assert code == 0
    plan = result(lines)
    assert plan["files"] == 3 and plan["mode"] == "folder"
    assert plan["folders"] == [{"folder": "Photography/Backups/LEG/Eclipse/Day 1", "files": 3}]
    assert media_files(env["back"]) == []
    code, lines = run(capsys, "backup", "--from-folder", str(folder), "--to", str(env["back"]))
    assert code == 0 and result(lines)["copied"] == 3
    out = env["back"] / "Photography" / "Backups" / "LEG" / "Eclipse" / "Day 1"
    assert sorted(p.name for p in out.iterdir()) == ["E1.NEF", "E2.NEF", "E3.NEF"]
    with catalog() as conn:        # the unhashed source files were hashed during the copy and are verified now
        rows = conn.execute("SELECT v.role, i.state, i.asset_id IS NOT NULL AS a FROM instances i "
                            "JOIN volumes v USING (volume_id) WHERE i.kind = 'raw'").fetchall()
    assert sorted((r["role"], r["state"], r["a"]) for r in rows).count(("LEGACY", "verified", 1)) == 3
    assert sorted((r["role"], r["state"], r["a"]) for r in rows).count(("MASTER", "verified", 1)) == 3
    assert not (env["leg"] / "Eclipse" / "Day 1" / ".pvpartial").exists() and partials(env["leg"]) == []
    code, lines = run(capsys, "backup", "--from-folder", str(folder), "--to", str(env["back"]))
    assert code == 0 and result(lines)["skipped_dup"] == 3 and result(lines)["copied"] == 0


def test_folder_source_hashed_file_is_checked_against_the_catalog(env, capsys) -> None:
    setup(capsys, env, import_card=False)
    folder = env["leg"] / "Eclipse"
    code, lines = run(capsys, "backup", "--from-folder", str(folder), "--to", str(env["back"]))
    assert code == 0
    victim = env["leg"] / "Eclipse" / "Day 1" / "E1.NEF"
    (env["back"] / "Photography" / "Backups" / "LEG" / "Eclipse" / "Day 1" / "E1.NEF").unlink()
    st = victim.stat()
    # Same size and mtime, different bytes: silent corruption on the source.
    data = bytearray(victim.read_bytes())
    data[0] ^= 0xFF
    victim.write_bytes(bytes(data))
    os.utime(victim, ns=(st.st_atime_ns, st.st_mtime_ns))
    env_before = tree_hashes(env["leg"])
    code, lines = run(capsys, "backup", "--from-folder", str(folder), "--to", str(env["back"]))
    assert code == 1 and any(e.get("code") == "source_mismatch" for e in lines)
    assert not (env["back"] / "Photography" / "Backups" / "LEG" / "Eclipse" / "Day 1" / "E1.NEF").exists()
    with catalog() as conn:
        assert conn.execute("SELECT state FROM instances WHERE rel_path LIKE '%E1.NEF' AND volume_id = "
                            "(SELECT volume_id FROM volumes WHERE role = 'LEGACY')").fetchone()[0] == "mismatch"
    assert tree_hashes(env["leg"]) == env_before
    # restore the bytes so the fixture's "source unchanged" check holds
    data[0] ^= 0xFF
    victim.write_bytes(bytes(data))
    os.utime(victim, ns=(st.st_atime_ns, st.st_mtime_ns))


# --------------------------------------------------------------------------- faults and refusals


def test_source_bytes_not_matching_the_catalog_fail_the_item(env, capsys) -> None:
    card_id = setup(capsys, env)
    victim = media_files(env["work"])[0]
    original = victim.read_bytes()
    victim.write_bytes(bytes([original[0] ^ 0xFF]) + original[1:])
    code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]))
    assert code == 1
    assert result(lines)["copied"] == N - 1 and result(lines)["failed"] == 1
    assert not (env["back"] / victim.relative_to(env["work"])).exists()
    with catalog() as conn:
        assert conn.execute("SELECT state FROM instances WHERE rel_path = ? AND volume_id = (SELECT volume_id "
                            "FROM volumes WHERE label = 'WORK')", (str(victim.relative_to(env["work"])),)
                            ).fetchone()[0] == "mismatch"
    assert partials(env["back"]) == []


def test_target_unplugged_mid_file_pauses_then_resume_completes(env, capsys, monkeypatch) -> None:
    card_id = setup(capsys, env)
    real = backup_mod.copy_verified
    calls = {"n": 0}

    def unplug(src, dst, **kw):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 3:
            (env["back"] / ".photovault-id").rename(env["back"] / ".marker-hidden")
            raise OSError(5, "Input/output error")
        return real(src, dst, **kw)

    monkeypatch.setattr(backup_mod, "copy_verified", unplug)
    code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]))
    assert code == 4 and any(e.get("code") == "drive_gone" for e in lines)
    assert partials(env["back"]) == []
    with catalog() as conn:
        job = conn.execute("SELECT job_id, state FROM jobs WHERE kind = 'replicate'").fetchone()
    assert job["state"] == "paused" and item_states(job["job_id"]) == {"committed": 2, "planned": N - 2}
    (env["back"] / ".marker-hidden").rename(env["back"] / ".photovault-id")
    monkeypatch.setattr(backup_mod, "copy_verified", real)
    code, lines = run(capsys, "resume", str(job["job_id"]))
    assert code == 0 and result(lines)["copied"] == N - 2
    assert card_tiers(env) == {"backed_up"}


def test_existing_file_same_bytes_is_registered_different_bytes_fail(env, capsys) -> None:
    card_id = setup(capsys, env)
    files = media_files(env["work"])
    same, other = files[0], files[1]
    (env["back"] / same.relative_to(env["work"])).parent.mkdir(parents=True)
    (env["back"] / same.relative_to(env["work"])).write_bytes(same.read_bytes())
    (env["back"] / other.relative_to(env["work"])).write_bytes(b"someone else's file")
    code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]))
    assert code == 1
    assert result(lines)["skipped_dup"] == 1 and result(lines)["failed"] == 1 and result(lines)["copied"] == N - 2
    assert (env["back"] / other.relative_to(env["work"])).read_bytes() == b"someone else's file"
    with catalog() as conn:
        assert conn.execute("SELECT state FROM instances WHERE volume_id = (SELECT volume_id FROM volumes "
                            "WHERE label = 'BACK') AND rel_path = ?", (str(same.relative_to(env["work"])),)
                            ).fetchone()[0] == "verified"


def test_second_job_onto_the_same_target_is_refused(env, capsys) -> None:
    card_id = setup(capsys, env)
    cfg = load_config()
    conn = db.connect(cfg.catalog_path)
    db.migrate(conn)
    out = Emitter(io.StringIO(), json_mode=True)
    conn.execute("BEGIN IMMEDIATE")
    backup_mod.plan_backup(conn, cfg, source=env["work"], target=env["back"], card_volume_id=card_id, emit=out)
    conn.execute("COMMIT")                                   # planned, not run
    code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]), "--dry-run")
    assert code == 2 and "already writing" in lines[-1]["message"]
    code, lines = backup(capsys, env, "--from-folder", str(env["leg"] / "Eclipse"))
    assert code == 2 and "already writing" in lines[-1]["message"]


def test_ntfs_and_legacy_targets_are_refused(env, capsys, monkeypatch) -> None:
    card_id = setup(capsys, env)
    code, lines = run(capsys, "backup", "--card", card_id, "--from", str(env["work"]), "--to", str(env["leg"]))
    assert code == 2 and ("never written to" in lines[-1]["message"] or "no PhotoVault marker" in lines[-1]["message"])
    for fs in ("ntfs", "ufsd_ntfs"):
        monkeypatch.setattr(volumes, "probe", lambda m, fs=fs, **_: _with_fs(fake_probe(m), fs))
        code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]))
        assert code == 2 and "only exFAT and APFS" in lines[-1]["message"]
    assert media_files(env["back"]) == []


def _with_fs(p: volumes.VolumeProbe, fs: str) -> volumes.VolumeProbe:
    from dataclasses import replace
    return replace(p, fs_type=fs)


def test_apfs_target_is_allowed_and_card_source_must_be_a_card(env, capsys, monkeypatch) -> None:
    card_id = setup(capsys, env)
    monkeypatch.setattr(volumes, "probe", lambda m, **_: _with_fs(fake_probe(m), "apfs"))
    code, lines = backup(capsys, env, "--card", card_id, "--from", str(env["work"]), "--dry-run")
    assert code == 0 and result(lines)["target_fs"] == "apfs"
    code, lines = backup(capsys, env, "--card", "not-a-card", "--from", str(env["work"]), "--dry-run")
    assert code == 2
