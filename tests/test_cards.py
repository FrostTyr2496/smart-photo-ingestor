"""Cards (spec `cards`): the Mac folder as a drive (card-1), card check and removability (card-2)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from photovault import drives, volumes
from photovault.catalog import db, repo
from photovault.protocol import Emitter


class _Quiet(Emitter):
    def __init__(self) -> None:
        self.events: list[dict] = []

    def emit(self, event: str, **fields) -> None:  # type: ignore[override]
        self.events.append({"event": event, **fields})


def _fake_run(*_a, **_k):
    import subprocess
    import plistlib
    return subprocess.CompletedProcess([], 0, plistlib.dumps({"FilesystemType": "APFS"}), b"")


# --------------------------------------------------------------------------- card-1: the Mac folder


def test_folder_inside_home_is_a_folder_volume(tmp_path: Path) -> None:
    home = Path.home()
    sandbox = home / "Sandbox"
    sandbox.mkdir()
    assert volumes.is_folder_volume(sandbox)
    assert volumes.is_folder_volume(Path("~/Sandbox"))
    assert not volumes.is_folder_volume(home)  # home itself is too broad
    assert not volumes.is_folder_volume(home / "nope")
    assert not volumes.is_folder_volume(tmp_path)  # outside home: probed as a mount, as before


def test_probe_folder_has_no_uuid_and_is_never_a_card() -> None:
    sandbox = Path.home() / "Sandbox"
    (sandbox / "DCIM").mkdir(parents=True)  # a card dump copied into the folder does not make it a card
    p = volumes.probe(sandbox, run=_fake_run)
    assert (p.da_volume_uuid, p.looks_like_card, p.fs_type, p.internal, p.name) == (None, False, "apfs", True, "Sandbox")
    assert p.capacity_bytes and p.marker_id is None


def test_mac_folder_registers_as_working_not_legacy(tmp_path: Path) -> None:
    sandbox = Path.home() / "Sandbox"
    sandbox.mkdir()
    conn = db.connect(tmp_path / "c.db")
    db.migrate(conn)
    p = volumes.probe(sandbox, run=_fake_run)
    with pytest.raises(ValueError, match="no volume UUID"):
        volumes.register_volume(conn, p, role="LEGACY")
    volumes.write_marker(sandbox, "mac-1")
    p = volumes.probe(sandbox, run=_fake_run)
    with repo.transaction(conn):
        vid = volumes.register_volume(conn, p, role="WORKING", label="This Mac")
    assert vid == "mac-1" and repo.get_volume(conn, vid)["da_volume_uuid"] is None


def test_scan_mounts_lists_the_mac_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(volumes, "probe_folder", lambda f, **_: volumes.VolumeProbe(
        Path(f), Path(f).name, "apfs", None, 10**9, True, volumes.read_marker(Path(f)), False))
    root = tmp_path / "Vols"
    root.mkdir()
    conn = db.connect(tmp_path / "c.db")
    db.migrate(conn)
    assert drives.scan_mounts(conn, root, _Quiet()) == []  # no ~/Sandbox yet
    sandbox = Path.home() / "Sandbox"
    sandbox.mkdir()
    [row] = drives.scan_mounts(conn, root, _Quiet())
    assert (row["mount"], row["label"], row["role"], row["local"], row["mounted"]) == (
        str(sandbox), "This Mac", "unassigned", True, True)
    assert drives.scan_mounts(conn, root, _Quiet(), include_mac=False) == []

    other = Path.home() / "Pictures" / "Work"
    other.mkdir(parents=True)
    with repo.transaction(conn):
        repo.set_setting(conn, "mac_folder", "~/Pictures/Work")
    [row] = drives.scan_mounts(conn, root, _Quiet())
    assert row["mount"] == str(other)
