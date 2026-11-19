"""fnd-2: catalog connection, migration runner and snapshot."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from photovault.catalog import db, migrations

# The ten tables of system-design section 5 (the task text said 11; the schema is authoritative), plus the
# four added by migration 2 (system-design section 5b).
EXPECTED_TABLES = {
    "volumes", "bodies", "shoots", "sequences", "capture_groups",
    "assets", "instances", "sidecars", "jobs", "job_items",
    "group_meta", "geo_points", "settings", "volume_usage",
    "trips", "shoot_plan_decisions", "shoot_apply_log", "body_clock",
}


def _tables(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return {r["name"] for r in rows}


@pytest.fixture
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "home" / "catalog.sqlite")
    yield c
    c.close()


def _add_volume(conn: sqlite3.Connection, volume_id: str = "v1", role: str = "WORKING") -> None:
    conn.execute(
        "INSERT INTO volumes (volume_id, role, label, created_at, last_seen_at) VALUES (?, ?, 'L', 't', 't')",
        (volume_id, role),
    )


def test_connect_creates_parent_dirs_and_pragmas(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "catalog.sqlite"
    c = db.connect(path)
    try:
        assert path.parent.is_dir()
        assert c.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert c.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert c.execute("PRAGMA synchronous").fetchone()[0] == 2  # FULL
        assert c.row_factory is sqlite3.Row
    finally:
        c.close()


def test_fresh_db_migrates_to_1_with_all_tables(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert db.migrate(conn) == len(migrations.MIGRATIONS) == 8
    assert _tables(conn) == EXPECTED_TABLES


def test_second_migrate_is_noop(conn: sqlite3.Connection) -> None:
    assert db.migrate(conn) == len(migrations.MIGRATIONS)
    _add_volume(conn)
    assert db.migrate(conn) == len(migrations.MIGRATIONS)
    assert conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0] == 1


def test_wal_is_on(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_foreign_key_violation_raises(conn: sqlite3.Connection) -> None:
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO instances (volume_id, rel_path, rel_path_key, size, mtime_ns, kind, state, last_seen_at)"
            " VALUES ('nope', 'a.nef', 'a.nef', 1, 1, 'raw', 'indexed', 't')"
        )


def test_bad_role_rejected_by_check(conn: sqlite3.Connection) -> None:
    db.migrate(conn)
    with pytest.raises(sqlite3.IntegrityError):
        _add_volume(conn, role="BOGUS")
    _add_volume(conn, role="CARD")  # a valid role still works


def test_failed_migration_rolls_back(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migrations, "MIGRATIONS", ["CREATE TABLE ok (x INTEGER); CREATE TABLE ok (y INTEGER);"])
    with pytest.raises(sqlite3.OperationalError, match="migration 1 failed"):
        db.migrate(conn)
    assert conn.execute("PRAGMA user_version").fetchone()[0] == 0
    assert "ok" not in _tables(conn)
    assert not conn.in_transaction


def test_newer_schema_version_refused(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA user_version=99")
    with pytest.raises(RuntimeError, match="newer than this engine"):
        db.migrate(conn)


def test_snapshot_opens_with_same_tables(conn: sqlite3.Connection, tmp_path: Path) -> None:
    db.migrate(conn)
    _add_volume(conn)
    dest = tmp_path / "snap" / "catalog-copy.sqlite"
    dest.parent.mkdir()
    db.snapshot(conn, dest)
    snap = sqlite3.connect(dest)
    snap.row_factory = sqlite3.Row
    try:
        assert _tables(snap) == EXPECTED_TABLES
        assert snap.execute("PRAGMA user_version").fetchone()[0] == len(migrations.MIGRATIONS)
        assert snap.execute("SELECT COUNT(*) FROM volumes").fetchone()[0] == 1
    finally:
        snap.close()


def test_snapshot_refuses_existing_dest(conn: sqlite3.Connection, tmp_path: Path) -> None:
    db.migrate(conn)
    dest = tmp_path / "exists.sqlite"
    dest.write_bytes(b"")
    with pytest.raises(FileExistsError):
        db.snapshot(conn, dest)


def test_v8_rebuilds_jobs_for_card_clear_and_keeps_every_row(conn: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    full = list(migrations.MIGRATIONS)
    monkeypatch.setattr(migrations, "MIGRATIONS", full[:7])
    assert db.migrate(conn) == 7
    conn.execute("INSERT INTO volumes VALUES ('v1','CARD','card:x',NULL,'U1',NULL,1,'/m','now','now')")
    conn.execute("INSERT INTO jobs (job_id, kind, source_volume_id, state, created_at) VALUES (5,'card_ingest','v1','done','now')")
    conn.execute("INSERT INTO job_items VALUES (5,1,'DCIM/a.NEF','a.NEF',NULL,NULL,NULL,'committed',NULL,'now')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO jobs (kind, state, created_at) VALUES ('card_clear','planned','now')")
    monkeypatch.setattr(migrations, "MIGRATIONS", full)
    assert db.migrate(conn) == 8
    assert conn.execute("SELECT job_id, kind, source_volume_id, state FROM jobs").fetchall()[0][:] == (5, "card_ingest", "v1", "done")
    assert conn.execute("SELECT COUNT(*) FROM job_items WHERE job_id = 5").fetchone()[0] == 1
    conn.execute("INSERT INTO jobs (kind, source_volume_id, state, created_at) VALUES ('card_clear','v1','planned','now')")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):  # job_items still points at jobs
        conn.execute("INSERT INTO job_items VALUES (99,1,'x',NULL,NULL,NULL,NULL,'planned',NULL,'now')")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO jobs (kind, state, created_at) VALUES ('bogus','planned','now')")
