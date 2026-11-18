"""Lifetime I/O metrics (met-1, D-054): recording, drive keys, batching, incidents, backfill and the CLI report."""

from __future__ import annotations

import errno
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest

from photovault import cli, copy_engine, hashing, metrics
from photovault.catalog import db

KIB = 1024


@pytest.fixture(autouse=True)
def _no_recorder():
    metrics.install(None)
    yield
    metrics.install(None)


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / "home"


def make_file(path: Path, size: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(os.urandom(size))
    return path


def store(home: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(metrics.store_path(home))
    conn.row_factory = sqlite3.Row
    return conn


def daily(home: Path, source: str = "live") -> dict[tuple[str, str], sqlite3.Row]:
    """(volume_key, op) -> row, summed over days (tests run inside one day)."""
    with store(home) as c:
        rows = c.execute("SELECT * FROM daily WHERE source = ?", (source,)).fetchall()
    return {(r["volume_key"], r["op"]): r for r in rows}


def by_op(home: Path, source: str = "live") -> dict[str, sqlite3.Row]:
    return {op: r for (_, op), r in daily(home, source).items()}


def new_catalog(path: Path) -> sqlite3.Connection:
    conn = db.connect(path)
    db.migrate(conn)
    return conn


def add_volume(conn: sqlite3.Connection, vid: str, label: str, mount: Path | str | None, role: str = "LEGACY") -> None:
    conn.execute("INSERT INTO volumes (volume_id, role, label, last_mount_path, created_at, last_seen_at) "
                 "VALUES (?, ?, ?, ?, '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                 (vid, role, label, str(mount) if mount is not None else None))


# ------------------------------------------------------------------------------------------ 1. recording


def test_records_through_real_primitives(tmp_path: Path, home: Path) -> None:
    big = make_file(tmp_path / "src" / "big.bin", 1_000_000)
    small = make_file(tmp_path / "src" / "small.bin", 5000)
    rec = metrics.Recorder(home, command="unit")
    metrics.install(rec)

    assert hashing.sha256_file(big) == hashlib.sha256(big.read_bytes()).hexdigest()
    hashing.partial_sha256(big)
    hashing.partial_sha256(small)
    dst = tmp_path / "dst" / "copy.bin"
    dst.parent.mkdir()
    copy_engine.copy_verified(big, dst)
    assert dst.read_bytes() == big.read_bytes()
    rec.close(0)

    ops = by_op(home)
    # read_full: the hash plus the copy's source read.
    assert ops["read_full"]["files"] == 2 and ops["read_full"]["bytes"] == 2_000_000
    assert ops["read_full"]["busy_s"] > 0
    # read_partial: 3 windows of 64 KiB for the big file, the whole file for the small one, plus the copy's
    # none (copy_verified does not partial-hash the source here).
    assert ops["read_partial"]["bytes"] >= 3 * 64 * KIB + 5000
    assert ops["read_partial"]["files"] >= 2
    assert ops["read_verify"]["bytes"] == 1_000_000 and ops["read_verify"]["busy_s"] > 0
    assert ops["write"]["bytes"] == 1_000_000 and ops["write"]["files"] == 1 and ops["write"]["busy_s"] > 0
    assert all(r["errors"] == 0 for r in ops.values())

    with store(home) as c:
        run = c.execute("SELECT * FROM runs").fetchall()
        live_since = c.execute("SELECT value FROM meta WHERE key='live_since'").fetchone()[0]
    assert len(run) == 1
    assert run[0]["command"] == "unit" and run[0]["exit_code"] == 0 and run[0]["finished_at"]
    assert run[0]["bytes_read"] == ops["read_full"]["bytes"] + ops["read_partial"]["bytes"] + 1_000_000
    assert run[0]["bytes_written"] == 1_000_000
    assert live_since == rec.started_at


def test_partial_bytes_exact(tmp_path: Path, home: Path) -> None:
    big = make_file(tmp_path / "big.bin", 1_000_000)
    small = make_file(tmp_path / "small.bin", 5000)
    rec = metrics.Recorder(home)
    metrics.install(rec)
    hashing.partial_sha256(big)
    rec.flush()
    assert by_op(home)["read_partial"]["bytes"] == 3 * 64 * KIB
    hashing.partial_sha256(small)
    rec.close(0)
    r = by_op(home)["read_partial"]
    assert r["bytes"] == 3 * 64 * KIB + 5000 and r["files"] == 2


def test_copy_attributes_sides_to_their_drives(tmp_path: Path, home: Path) -> None:
    vols = tmp_path / "Vols"
    src = make_file(vols / "SRC" / "a.bin", 300_000)
    (vols / "DST").mkdir()
    rec = metrics.Recorder(home, volumes_root=vols)
    metrics.install(rec)
    copy_engine.copy_verified(src, vols / "DST" / "a.bin")
    rec.close(0)
    d = daily(home)
    assert d[(f"path:{vols}/SRC", "read_full")]["bytes"] == 300_000
    assert d[(f"path:{vols}/DST", "write")]["bytes"] == 300_000
    assert d[(f"path:{vols}/DST", "read_verify")]["bytes"] == 300_000


# --------------------------------------------------------------------------------------- 2. no recorder


def test_no_recorder_means_no_store(tmp_path: Path, home: Path) -> None:
    f = make_file(tmp_path / "f.bin", 10_000)
    assert metrics.current() is None
    assert hashing.sha256_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()
    hashing.partial_sha256(f)
    copy_engine.copy_verified(f, tmp_path / "g.bin")
    metrics.incident("read_full", f, "x", "y")
    assert not metrics.store_path(home).exists()
    assert not list(tmp_path.rglob("metrics.sqlite"))


# --------------------------------------------------------------------------------------- 3. drive keys


def test_drive_key_resolution(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mnt = tmp_path / "mnt"
    drive, nested = mnt / "drive", mnt / "drive" / "nested"
    drive2 = mnt / "drive2"
    for d in (drive, nested, drive2):
        d.mkdir(parents=True)
    cat = home / "catalog.sqlite"
    conn = new_catalog(cat)
    add_volume(conn, "vol-a", "Alpha", drive)
    add_volume(conn, "vol-n", "Nested", nested)
    add_volume(conn, "vol-none", "Unmounted", None)
    conn.close()

    vroot = tmp_path / "Vols"
    (vroot / "Stray").mkdir(parents=True)
    rec = metrics.Recorder(home, cat, volumes_root=vroot)

    assert rec.resolve(drive / "x.nef") == ("vol-a", "Alpha")
    assert rec.resolve(drive / "sub" / "x.nef") == ("vol-a", "Alpha")
    assert rec.resolve(nested / "x.nef") == ("vol-n", "Nested")  # longest prefix wins
    assert rec.resolve(nested / "deep" / "x.nef") == ("vol-n", "Nested")
    # A sibling sharing a string prefix is not inside the drive.
    assert rec.resolve(drive2 / "x.nef")[0] == metrics.LOCAL_KEY
    assert rec.resolve(vroot / "Stray" / "a" / "b.nef") == (f"path:{vroot}/Stray", "Stray")
    assert rec.resolve(tmp_path / "elsewhere" / "x.nef") == (metrics.LOCAL_KEY, "Mac")


def test_volume_registered_later_is_picked_up(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mnt = tmp_path / "mnt" / "late"
    mnt.mkdir(parents=True)
    cat = home / "catalog.sqlite"
    conn = new_catalog(cat)
    rec = metrics.Recorder(home, cat, volumes_root=tmp_path / "Vols")
    monkeypatch.setattr(metrics, "VOLUME_RELOAD_S", 1000.0)
    assert rec.resolve(mnt / "a.nef")[0] == metrics.LOCAL_KEY
    add_volume(conn, "vol-late", "Late", mnt)
    # Within the reload window the miss is cached.
    assert rec.resolve(mnt / "a.nef")[0] == metrics.LOCAL_KEY
    monkeypatch.setattr(metrics, "VOLUME_RELOAD_S", 0.0)
    assert rec.resolve(mnt / "a.nef") == ("vol-late", "Late")
    conn.close()


# ----------------------------------------------------------------------------------- 4. flush batching


def test_flush_batching(tmp_path: Path, home: Path) -> None:
    f = make_file(tmp_path / "f.bin", 20_000)
    rec = metrics.Recorder(home, flush_s=1e9)
    metrics.install(rec)
    hashing.sha256_file(f)
    hashing.sha256_file(f)
    assert not metrics.store_path(home).exists()  # buffered only
    assert rec.flush() is True
    assert by_op(home)["read_full"]["files"] == 2
    hashing.sha256_file(f)
    hashing.sha256_file(f)
    hashing.sha256_file(f)
    assert by_op(home)["read_full"]["files"] == 2  # still buffered
    rec.flush()
    rec.flush()
    rec.close(0)
    rec.close(0)  # idempotent
    r = by_op(home)["read_full"]
    assert r["files"] == 5 and r["bytes"] == 100_000
    with store(home) as c:
        assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_flush_zero_writes_immediately(tmp_path: Path, home: Path) -> None:
    f = make_file(tmp_path / "f.bin", 20_000)
    rec = metrics.Recorder(home, flush_s=0)
    metrics.install(rec)
    hashing.sha256_file(f)
    assert by_op(home)["read_full"]["files"] == 1
    hashing.sha256_file(f)
    assert by_op(home)["read_full"]["files"] == 2
    rec.close(0)
    assert by_op(home)["read_full"]["files"] == 2 and by_op(home)["read_full"]["bytes"] == 40_000


# ------------------------------------------------------------------------------------- 5. failing store


def test_unwritable_store_never_breaks_a_hash(tmp_path: Path) -> None:
    blocker = tmp_path / "home-is-a-file"
    blocker.write_text("x")
    f = make_file(tmp_path / "f.bin", 20_000)
    rec = metrics.Recorder(blocker, flush_s=0)
    metrics.install(rec)
    assert hashing.sha256_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()
    rec.close(0)  # must not raise
    assert rec.flush_failures > 0


def test_store_open_raising_is_swallowed(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_home):  # noqa: ANN001
        raise sqlite3.OperationalError("disk gone")

    monkeypatch.setattr(metrics, "open_store", boom)
    f = make_file(tmp_path / "f.bin", 1000)
    rec = metrics.Recorder(home, flush_s=0)
    metrics.install(rec)
    assert hashing.sha256_file(f) == hashlib.sha256(f.read_bytes()).hexdigest()
    rec.close(1)
    assert rec.flush_failures > 0
    assert not metrics.store_path(home).exists()


# ------------------------------------------------------------------------------------------ 6. EIO


def test_eio_is_recorded_and_propagates(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    f = make_file(tmp_path / "bad.bin", 1000)

    def eio_open(path, *a, **kw):  # noqa: ANN001
        raise OSError(errno.EIO, "Input/output error", str(path))

    monkeypatch.setattr(hashing, "open", eio_open, raising=False)
    rec = metrics.Recorder(home)
    metrics.install(rec)
    with pytest.raises(OSError) as ei:
        hashing.sha256_file(f)
    assert ei.value.errno == errno.EIO
    rec.close(1)

    r = by_op(home)["read_full"]
    assert r["errors"] == 1 and r["files"] == 0
    with store(home) as c:
        inc = c.execute("SELECT * FROM incidents").fetchall()
    assert len(inc) == 1
    assert inc[0]["kind"] == "EIO" and inc[0]["errno"] == 5 and inc[0]["op"] == "read_full"
    assert inc[0]["path"] == str(f)


# ------------------------------------------------------------------------------- 7. verify mismatch


def test_verify_mismatch_incident(tmp_path: Path, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    src = make_file(tmp_path / "src.bin", 10_000)
    dst = tmp_path / "out" / "dst.bin"
    dst.parent.mkdir()
    monkeypatch.setattr(copy_engine, "_hash_uncached", lambda p: "0" * 64)
    rec = metrics.Recorder(home)
    metrics.install(rec)
    with pytest.raises(copy_engine.CopyError):
        copy_engine.copy_verified(src, dst)
    rec.close(1)
    assert not dst.exists()
    with store(home) as c:
        inc = c.execute("SELECT kind, errno, op FROM incidents").fetchall()
    assert [(i["kind"], i["errno"], i["op"]) for i in inc] == [("verify_mismatch", None, "read_verify")]
    ops = by_op(home)
    assert ops["read_verify"]["errors"] == 1
    assert ops["write"]["bytes"] == 10_000 and ops["write"]["errors"] == 0


# ---------------------------------------------------------------------------------------- 8. backfill

OLD = "2026-01-05T10:00:00Z"
LIVE = "2026-07-01T10:00:00Z"
LIVE_SINCE = "2026-06-01T00:00:00Z"


def build_backfill_catalog(path: Path) -> None:
    conn = new_catalog(path)
    add_volume(conn, "v-src", "SourceDrive", "/nonexistent/src", "LEGACY")
    add_volume(conn, "v-work", "WorkDrive", "/nonexistent/work", "WORKING")
    for i, (size, sha) in enumerate([(1_000_000, "a"), (100_000, "b"), (2_000_000, "c")], start=1):
        conn.execute("INSERT INTO assets (asset_id, sha256, size, partial_sha256, kind, first_seen_at) "
                     "VALUES (?, ?, ?, 'p', 'raw', ?)", (i, sha * 64, size, OLD))

    def inst(vol, rel, size, asset, state, verified_at, partial, seen):  # noqa: ANN001
        conn.execute("INSERT INTO instances (asset_id, volume_id, rel_path, rel_path_key, size, mtime_ns, "
                     "partial_sha256, kind, state, verified_at, last_seen_at) VALUES (?,?,?,?,?,1,?, 'raw',?,?,?)",
                     (asset, vol, rel, rel.lower(), size, partial, state, verified_at, seen))

    # Legacy drive: hashed (verified) once and partial-hashed.
    inst("v-src", "a/one.nef", 1_000_000, 1, "verified", OLD, "pp", OLD)
    # Legacy: small file, partial hash only (bytes = min(size, 192 KiB)).
    inst("v-src", "a/two.nef", 100_000, 2, "indexed", None, "pp", OLD)
    # Unreadable: mismatch with no asset.
    inst("v-src", "a/bad.nef", 500, None, "mismatch", None, None, OLD)
    # Ingest target: verified, matches a committed job item -> a write, not a read_full.
    inst("v-work", "2026/c.nef", 2_000_000, 3, "verified", OLD, "pp", OLD)
    # Verified after live_since: excluded.
    inst("v-src", "a/new.nef", 1_000_000, 1, "verified", LIVE, None, LIVE)
    conn.execute("INSERT INTO jobs (job_id, kind, source_volume_id, target_volume_id, state, created_at) "
                 "VALUES (1, 'card_ingest', 'v-src', 'v-work', 'done', ?)", (OLD,))
    conn.execute("INSERT INTO job_items (job_id, seq, src_rel, dst_rel, asset_id, state, updated_at) "
                 "VALUES (1, 1, 'DCIM/c.nef', '2026/c.nef', 3, 'committed', ?)", (OLD,))
    conn.execute("INSERT INTO job_items (job_id, seq, src_rel, dst_rel, asset_id, state, updated_at) "
                 "VALUES (1, 2, 'DCIM/d.nef', '2026/d.nef', 3, 'failed', ?)", (OLD,))
    conn.execute("INSERT INTO job_items (job_id, seq, src_rel, dst_rel, asset_id, state, updated_at) "
                 "VALUES (1, 3, 'DCIM/e.nef', '2026/e.nef', 3, 'committed', ?)", (LIVE,))
    conn.close()


def backfill_rows(home: Path) -> list[tuple]:
    with store(home) as c:
        return [tuple(r) for r in c.execute(
            "SELECT day, volume_key, op, files, bytes, errors FROM daily WHERE source='backfill' "
            "ORDER BY day, volume_key, op")]


def test_backfill(tmp_path: Path, home: Path) -> None:
    cat = home / "catalog.sqlite"
    build_backfill_catalog(cat)
    with metrics.open_store(home) as c:
        c.execute("INSERT INTO meta (key, value) VALUES ('live_since', ?)", (LIVE_SINCE,))
    before = cat.read_bytes()

    out = metrics.backfill(home, cat)
    day = metrics._local_day(OLD)
    rows = {(k, op): (files, nbytes, errs) for d, k, op, files, nbytes, errs in backfill_rows(home) if d == day}
    assert out["live_since"] == LIVE_SINCE
    assert rows[("v-work", "write")] == (1, 2_000_000, 0)
    assert ("v-work", "read_full") not in rows  # the ingest target is not a separate read
    # read_full for v-src: bytes 1 MB from one.nef plus the bad.nef error row (0 bytes).
    assert rows[("v-src", "read_full")] == (1, 1_000_000, 1)
    # read_partial: one.nef -> 192 KiB, two.nef -> 100_000 (both < live_since).
    assert rows[("v-src", "read_partial")] == (2, 192 * KIB + 100_000, 0)
    assert rows[("v-work", "read_partial")] == (1, 192 * KIB, 0)
    # Nothing at or after live_since.
    assert all(d == day for d, *_ in backfill_rows(home))
    assert out["errors"] == 1 and out["files_written"] == 1 and out["files_read"] == 1
    assert out["rows"] == len(backfill_rows(home))
    with store(home) as c:
        labels = dict(c.execute("SELECT volume_key, label FROM drives").fetchall())
    assert labels == {"v-src": "SourceDrive", "v-work": "WorkDrive"}
    assert cat.read_bytes() == before  # catalog is opened read-only

    first = backfill_rows(home)
    again = metrics.backfill(home, cat)
    assert backfill_rows(home) == first and again["rows"] == out["rows"]  # idempotent, replaces


def test_backfill_leaves_live_rows_and_sets_live_since(tmp_path: Path, home: Path) -> None:
    cat = home / "catalog.sqlite"
    build_backfill_catalog(cat)
    f = make_file(tmp_path / "f.bin", 1000)
    rec = metrics.Recorder(home)
    metrics.install(rec)
    hashing.sha256_file(f)
    rec.close(0)
    live = {k: tuple(r) for k, r in daily(home).items()}
    metrics.backfill(home, cat)
    # live_since is "now", so all the OLD and LIVE-dated catalog rows count as backfill.
    assert {k: tuple(r) for k, r in daily(home).items()} == live
    assert any(op == "write" for _, _, op, *_ in backfill_rows(home))
    with store(home) as c:
        assert c.execute("SELECT value FROM meta WHERE key='live_since'").fetchone()[0] == rec.started_at


# ------------------------------------------------------------------------------------------ 9. CLI


def run(capsys: pytest.CaptureFixture, *args: str) -> tuple[int, list[dict]]:
    code = cli.main(["--json", *args])
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return code, lines


@pytest.fixture
def cli_home(home: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("PHOTOVAULT_HOME", str(home))
    return home


def test_cli_metrics_report(tmp_path: Path, cli_home: Path, capsys: pytest.CaptureFixture) -> None:
    f = make_file(tmp_path / "f.bin", 50_000)
    rec = metrics.Recorder(cli_home, command="hash test")
    metrics.install(rec)
    hashing.sha256_file(f)
    rec.close(0)
    metrics.install(None)

    code, lines = run(capsys, "metrics", "--days", "7")
    assert code == 0
    res = [e for e in lines if e["event"] == "result"][-1]
    assert res["totals"]["files_read"] == 1 and res["totals"]["bytes_read"] == 50_000
    assert res["totals"]["errors"] == 0
    assert res["live_since"] == rec.started_at
    assert [d["label"] for d in res["drives"]] == ["Mac"]
    assert res["drives"][0]["bytes_read"] == 50_000
    assert len(res["days"]) == 1 and res["days"][0]["bytes_read"] == 50_000
    assert res["runs"][0]["command"] == "hash test" and res["runs"][0]["exit_code"] == 0
    assert metrics.current() is None

    code = cli.main(["metrics", "--days", "7"])  # human mode
    assert code == 0
    assert capsys.readouterr().out.strip()
    # Reporting must not record itself.
    with store(cli_home) as c:
        assert c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_cli_metrics_report_without_store(cli_home: Path, capsys: pytest.CaptureFixture) -> None:
    code, lines = run(capsys, "metrics")
    res = [e for e in lines if e["event"] == "result"][-1]
    assert code == 0 and res["drives"] == [] and res["live_since"] is None
    assert not metrics.store_path(cli_home).exists()


def test_cli_backfill(cli_home: Path, capsys: pytest.CaptureFixture) -> None:
    code, lines = run(capsys, "metrics", "backfill")
    assert code == 1
    assert any(e["event"] == "error" for e in lines)
    build_backfill_catalog(cli_home / "catalog.sqlite")
    code, lines = run(capsys, "metrics", "backfill")
    assert code == 0
    res = [e for e in lines if e["event"] == "result"][-1]
    assert res["rows"] > 0 and res["files_written"] == 2  # OLD and LIVE items, since live_since is now


def test_cli_command_without_io_leaves_no_trace(cli_home: Path, capsys: pytest.CaptureFixture) -> None:
    code, _ = run(capsys, "catalog", "init")
    assert code == 0
    assert metrics.current() is None
    assert not metrics.store_path(cli_home).exists()


def test_cli_installs_recorder_for_io_commands(cli_home: Path, capsys: pytest.CaptureFixture,
                                               monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list = []
    f = make_file(cli_home.parent / "f.bin", 4000)

    def fake_cmd(args, cfg, emit) -> int:  # noqa: ANN001
        seen.append(metrics.current())
        hashing.sha256_file(f)
        return 0

    monkeypatch.setattr(cli, "cmd_catalog_init", fake_cmd)
    assert cli.main(["--json", "catalog", "init"]) == 0
    assert seen and isinstance(seen[0], metrics.Recorder)
    assert metrics.current() is None
    with store(cli_home) as c:
        r = c.execute("SELECT command, exit_code, bytes_read FROM runs").fetchall()
    assert len(r) == 1 and r[0]["exit_code"] == 0 and r[0]["bytes_read"] == 4000
    assert r[0]["command"] == "--json catalog init"
