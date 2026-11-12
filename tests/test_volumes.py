"""fnd-4: volume probe, card detection, markers and registration. Uses tmp_path mounts and fake plists."""

from __future__ import annotations

import dataclasses
import plistlib
import subprocess
from pathlib import Path

import pytest

from photovault import volumes
from photovault.catalog import db, repo
from photovault.volumes import CardWriteRefused, UnknownVolume


@pytest.fixture
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "home" / "catalog.sqlite")
    db.migrate(c)
    yield c
    c.close()


def _mount(tmp_path: Path, name: str, *, card: bool = False) -> Path:
    m = tmp_path / name
    m.mkdir()
    if card:
        (m / "DCIM").mkdir()
    return m


def _fake_run(info: dict | None, returncode: int = 0, stderr: bytes = b""):
    calls: list[list[str]] = []

    def run(cmd, **kw):
        calls.append(cmd)
        out = plistlib.dumps(info) if info is not None else b""
        return subprocess.CompletedProcess(cmd, returncode, stdout=out, stderr=stderr)

    run.calls = calls
    return run


def _probe(mount: Path, uuid_: str | None = "FAKE-UUID-1", name: str = "Disk", **extra):
    info = {"VolumeName": name, "FilesystemType": "ExFAT", "TotalSize": 1000, "Internal": False}
    if uuid_:
        info["VolumeUUID"] = uuid_
    info.update(extra)
    return volumes.probe(mount, run=_fake_run(info))


def test_looks_like_card(tmp_path: Path) -> None:
    assert volumes.looks_like_card(_mount(tmp_path, "card", card=True))
    assert not volumes.looks_like_card(_mount(tmp_path, "ssd"))


def test_probe_parses_plist_and_calls_diskutil(tmp_path: Path) -> None:
    m = _mount(tmp_path, "card", card=True)
    run = _fake_run({"VolumeName": "NIKON", "FilesystemType": "ExFAT", "VolumeUUID": "U1", "TotalSize": 64,
                     "Size": 99, "Internal": True})
    p = volumes.probe(m, run=run)
    assert run.calls == [["diskutil", "info", "-plist", str(m)]]
    assert (p.name, p.fs_type, p.da_volume_uuid, p.capacity_bytes, p.internal) == ("NIKON", "exfat", "U1", 64, True)
    assert p.looks_like_card and p.marker_id is None and p.mount == m


def test_probe_missing_keys_and_size_fallback(tmp_path: Path) -> None:
    m = _mount(tmp_path, "thing")
    p = volumes.probe(m, run=_fake_run({"Size": 7}))
    assert (p.name, p.fs_type, p.da_volume_uuid, p.capacity_bytes, p.internal) == ("thing", "", None, 7, False)
    p = volumes.probe(m, run=_fake_run({}))
    assert p.capacity_bytes is None


def test_probe_errors(tmp_path: Path) -> None:
    m = _mount(tmp_path, "thing")
    with pytest.raises(RuntimeError, match="diskutil info failed"):
        volumes.probe(m, run=_fake_run(None, returncode=1, stderr=b"no such disk"))
    with pytest.raises(RuntimeError, match="could not parse"):
        volumes.probe(m, run=_fake_run(None))


def test_marker_round_trip(tmp_path: Path) -> None:
    m = _mount(tmp_path, "ssd")
    assert volumes.read_marker(m) is None
    volumes.write_marker(m, "vol-1")
    assert volumes.read_marker(m) == "vol-1"
    volumes.write_marker(m, "vol-1")  # same id again is a no-op
    assert [p.name for p in m.iterdir()] == [".photovault-id"]  # no temp file left behind
    assert _probe(m).marker_id == "vol-1"


def test_write_marker_refuses_card(tmp_path: Path) -> None:
    m = _mount(tmp_path, "card", card=True)
    with pytest.raises(CardWriteRefused):
        volumes.write_marker(m, "vol-1")
    assert not (m / ".photovault-id").exists()


def test_write_marker_refuses_different_id(tmp_path: Path) -> None:
    m = _mount(tmp_path, "ssd")
    volumes.write_marker(m, "vol-1")
    with pytest.raises(FileExistsError):
        volumes.write_marker(m, "vol-2")
    assert volumes.read_marker(m) == "vol-1"


def test_read_marker_malformed_raises(tmp_path: Path) -> None:
    m = _mount(tmp_path, "ssd")
    (m / ".photovault-id").write_text("not json")
    with pytest.raises(ValueError):
        volumes.read_marker(m)
    with pytest.raises(ValueError):
        volumes.write_marker(m, "vol-1")


def test_register_unknown_volume_needs_role(conn, tmp_path: Path) -> None:
    with pytest.raises(UnknownVolume):
        volumes.register_volume(conn, _probe(_mount(tmp_path, "mystery")))
    assert conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0] == 0


def test_register_card_creates_card_and_matches_by_uuid(conn, tmp_path: Path) -> None:
    m = _mount(tmp_path, "card", card=True)
    vid = volumes.register_volume(conn, _probe(m, "CARD-UUID", name="NIKON Z 6_2"))
    row = repo.find_volume(conn, da_volume_uuid="CARD-UUID")
    assert row["volume_id"] == vid and row["role"] == "CARD" and row["label"] == "card:NIKON Z 6_2"
    assert row["marker_id"] is None
    assert volumes.register_volume(conn, _probe(m, "CARD-UUID", name="NIKON Z 6_2")) == vid
    assert conn.execute("SELECT COUNT(*) FROM volumes").fetchone()[0] == 1
    # an in-camera format changes the UUID, which makes a new CARD volume (section 7)
    assert volumes.register_volume(conn, _probe(m, "NEW-UUID", name="NIKON Z 6_2")) != vid


def test_register_working_needs_marker_and_uses_it_as_id(conn, tmp_path: Path) -> None:
    m = _mount(tmp_path, "ssd")
    with pytest.raises(ValueError, match="marker"):
        volumes.register_volume(conn, _probe(m, "U-W"), role="WORKING")
    volumes.write_marker(m, "work-1")
    vid = volumes.register_volume(conn, _probe(m, "U-W", name="X9 Pro"), role="WORKING")
    row = repo.find_volume(conn, marker_id="work-1")
    assert vid == "work-1" and row["role"] == "WORKING" and row["marker_id"] == "work-1"
    assert row["label"] == "X9 Pro" and row["fs_type"] == "exfat" and row["capacity_bytes"] == 1000


def test_rename_keeps_id_and_updates_mount_path(conn, tmp_path: Path) -> None:
    m = _mount(tmp_path, "ssd")
    volumes.write_marker(m, "work-1")
    vid = volumes.register_volume(conn, _probe(m, "U-W", name="Old Name"), role="WORKING")
    m2 = m.rename(tmp_path / "renamed")
    again = volumes.register_volume(conn, _probe(m2, "U-W", name="New Name"))
    assert again == vid
    row = repo.find_volume(conn, marker_id="work-1")
    assert row["last_mount_path"] == str(m2) and row["label"] == "Old Name"
    # matches by marker alone even if the UUID is gone
    assert volumes.register_volume(conn, _probe(m2, None, name="New Name")) == vid


def test_register_legacy_by_uuid_no_marker(conn, tmp_path: Path) -> None:
    m = _mount(tmp_path, "flats")
    with pytest.raises(ValueError, match="UUID"):
        volumes.register_volume(conn, _probe(m, None), role="LEGACY")
    vid = volumes.register_volume(conn, _probe(m, "U-L", name="Flats 1"), role="LEGACY")
    assert repo.find_volume(conn, da_volume_uuid="U-L")["marker_id"] is None
    assert volumes.register_volume(conn, _probe(m, "U-L")) == vid


def test_register_role_conflicts(conn, tmp_path: Path) -> None:
    card = _mount(tmp_path, "card", card=True)
    with pytest.raises(ValueError, match="looks like a camera card"):
        volumes.register_volume(conn, _probe(card, "U-C"), role="WORKING")
    vid = volumes.register_volume(conn, _probe(card, "U-C"))
    with pytest.raises(ValueError, match="already registered"):
        volumes.register_volume(conn, _probe(card, "U-C"), role="LEGACY")
    with pytest.raises(ValueError, match="unknown role"):
        volumes.register_volume(conn, _probe(card, "U-C"), role="BOGUS")
    assert vid


def test_probe_is_frozen(tmp_path: Path) -> None:
    p = _probe(_mount(tmp_path, "x"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        p.name = "y"  # type: ignore[misc]
