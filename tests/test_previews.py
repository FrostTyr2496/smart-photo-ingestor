"""act-4: previews cached on the Mac from a connected drive, served while it is unplugged, and the action that asks."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from photovault import actions, cli, previews
from photovault.catalog import db
from photovault.config import load_config
from photovault.web import server as web
from tests.test_cli import result, run
from tests.test_shoots import Builder, d
from tests.test_web_api import Client, srv  # noqa: F401  (the server fixture)

JPEG = b"\xff\xd8 fake preview"


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "c.sqlite")
    db.migrate(c)
    c.execute("INSERT INTO volumes (volume_id, role, label, created_at, last_seen_at) "
              "VALUES ('v1', 'WORKING', 'Working SSD', 'x', 'x')")
    yield c
    c.close()


class FakeReader:
    """Stands in for the persistent ExifTool: a preview for every file except names containing 'nopreview'."""
    reads: list[Path] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def read(self, path, tags=()):
        FakeReader.reads.append(Path(path))
        return None if "nopreview" in str(path) else JPEG + str(path).encode()


class Emit:
    def __init__(self):
        self.events = []

    def emit(self, event, **kw):
        self.events.append({"event": event, **kw})


def drive(tmp_path: Path, conn) -> tuple[Path, list[int]]:
    """Five photos on v1 (files on disk), plus one indexed but not hashed (found through its index job)."""
    ids = Builder(conn).frames(d(9, 20), 5, path="Photos/Dunes")
    mount = tmp_path / "WORK"
    for g in ids:
        f = mount / "Photos" / "Dunes" / f"f{g}.nef"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"raw")
    conn.execute("INSERT INTO capture_groups (group_id, body_id, shutter_count, capture_time, time_source, "
                 "canonical_stem, original_stem, created_at) VALUES (99, NULL, NULL, '2023-09-10T10:00:00', 'exif', "
                 "'u', 'u', 'x')")
    job = conn.execute("INSERT INTO jobs (kind, source_volume_id, state, created_at) "
                       "VALUES ('index_volume', 'v1', 'done', 'x')").lastrowid
    conn.execute("INSERT INTO job_items (job_id, seq, src_rel, group_id, role, state, updated_at) "
                 "VALUES (?, 1, 'Later/nopreview.nef', 99, 'primary', 'planned', 'x')", (job,))
    (mount / "Later").mkdir()
    (mount / "Later" / "nopreview.nef").write_bytes(b"raw")
    return mount, ids


def test_volume_primaries_include_unhashed_files(tmp_path, conn) -> None:
    _, ids = drive(tmp_path, conn)
    rows = previews.volume_primaries(conn, "v1")
    assert [g for g, _ in rows] == [*ids, 99]
    assert rows[-1] == (99, "Later/nopreview.nef")


def test_cache_volume_adds_skips_and_resumes(tmp_path, conn) -> None:
    mount, ids = drive(tmp_path, conn)
    home = tmp_path / "home"
    previews.store(home, ids[0], b"\xff\xd8 already")
    (mount / "Photos" / "Dunes" / f"f{ids[1]}.nef").unlink()  # gone from the drive
    FakeReader.reads = []
    emit = Emit()
    out = previews.cache_volume(conn, home, mount, "v1", emit, reader=FakeReader)
    assert out == {"photos": 6, "already": 1, "added": 3, "no_preview": 1, "missing": 1,
                   "bytes": out["bytes"], "paused": False}
    assert out["bytes"] > 0 and previews.read_cached(home, ids[0]) == b"\xff\xd8 already"
    assert previews.read_cached(home, ids[2]).startswith(JPEG)
    assert previews.cached_ids(home) == {ids[0], ids[2], ids[3], ids[4]}
    assert emit.events[-1]["current"] == emit.events[-1]["total"] == 5
    assert all(p.is_relative_to(mount.resolve()) for p in FakeReader.reads)
    # nothing written beside the photos
    assert sorted(p.name for p in (mount / "Photos" / "Dunes").iterdir()) == sorted(
        f"f{g}.nef" for g in ids if g != ids[1])
    # a second run reads only what is still missing
    FakeReader.reads = []
    again = previews.cache_volume(conn, home, mount, "v1", Emit(), reader=FakeReader)
    assert (again["added"], again["already"]) == (0, 4) and len(FakeReader.reads) == 1


def test_cache_volume_pauses_and_refuses_paths_outside_the_drive(tmp_path, conn) -> None:
    mount, ids = drive(tmp_path, conn)
    conn.execute("UPDATE instances SET rel_path = '../outside.nef' WHERE asset_id = ?", (ids[0],))
    (tmp_path / "outside.nef").write_bytes(b"raw")
    home = tmp_path / "home"
    out = previews.cache_volume(conn, home, mount, "v1", Emit(), reader=FakeReader)
    assert out["missing"] == 1 and ids[0] not in previews.cached_ids(home)
    stop = iter([False, True])
    paused = previews.cache_volume(conn, tmp_path / "home2", mount, "v1", Emit(), reader=FakeReader,
                                   should_stop=lambda: next(stop, True))
    assert paused["paused"] and paused["added"] == 0 and paused["missing"] == 1


def test_action_asks_only_for_connected_drives_with_photos_left(tmp_path, conn) -> None:
    mount, ids = drive(tmp_path, conn)
    home = tmp_path / "home"
    now = datetime(2026, 11, 18, tzinfo=UTC)
    kinds = lambda found: [a for a in found if a["kind"] == "previews_missing"]  # noqa: E731
    assert kinds(actions.compute(conn, now=now, mounted={}, home=home)) == []
    found = kinds(actions.compute(conn, now=now, mounted={"v1": str(mount)}, home=home))
    assert len(found) == 1
    a = found[0]
    assert a["id"] == "previews-missing:v1" and a["severity"] == "low" and a["evidence"]["files"] == 6
    assert a["do"]["body"] == {"cmd": "previews_cache", "args": {"mount": str(mount)}}
    assert "Working SSD" in a["title"] and a["cli"] == f'uv run photovault previews cache "{mount}"'
    for g in [*ids, 99]:
        previews.store(home, g, JPEG)
    assert kinds(actions.compute(conn, now=now, mounted={"v1": str(mount)}, home=home)) == []


def test_build_argv_and_cli(tmp_path, monkeypatch, capsys) -> None:
    argv = web.build_argv("previews_cache", {"mount": "/mnt/x"})
    assert argv == ["previews", "cache", "/mnt/x"]
    cli.build_parser().parse_args(["--json", *argv])
    with pytest.raises(web.BadRequest):
        web.build_argv("previews_cache", {"mount": "relative"})

    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    cfg = load_config()
    c = db.connect(cfg.catalog_path)
    db.migrate(c)
    c.execute("INSERT INTO volumes (volume_id, role, label, created_at, last_seen_at) "
              "VALUES ('v1', 'WORKING', 'Working SSD', 'x', 'x')")
    mount, ids = drive(tmp_path, c)
    c.close()
    monkeypatch.setattr(cli, "registered_volume", lambda conn, m: {"volume_id": "v1", "label": "Working SSD"})
    monkeypatch.setattr(previews, "PreviewReader", FakeReader)
    code, lines = run(capsys, "previews", "cache", str(mount))
    r = result(lines)
    assert code == 0 and (r["volume"], r["added"], r["no_preview"]) == ("Working SSD", 5, 1)
    assert previews.cached_ids(cfg.home) == set(ids)


def test_thumb_is_served_from_the_cache_while_unplugged(srv: Client) -> None:  # noqa: F811
    conn = db.connect(srv.app.catalog_path)
    conn.execute("INSERT INTO capture_groups (group_id, body_id, shutter_count, capture_time, time_source, "
                 "canonical_stem, original_stem, created_at) VALUES (4242, NULL, NULL, '2023-09-10T10:00:00', "
                 "'exif', 'c', 'c', 'x')")
    conn.close()
    assert srv.call("GET", "/api/groups/4242/thumb")[0] == 404  # no drive, no cache
    previews.store(srv.home, 4242, JPEG)
    status, body = srv.call("GET", "/api/groups/4242/thumb")
    assert status == 200 and body == JPEG
