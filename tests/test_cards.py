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


# --------------------------------------------------------------------------- card-2: check and removability

import shutil  # noqa: E402

from photovault import cards, metadata  # noqa: E402
from photovault.config import load_config  # noqa: E402
from tests.test_cli import fake_probe, fake_read, result, run, tree_hashes  # noqa: E402


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    """MASTER holds A, WORKING holds B, LEGACY holds C. The card holds A, B, C and a new D (Z 6_2 folder) and
    E (Z 8 folder, only on the card)."""
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", fake_read)
    vols = tmp_path / "Vols"
    data = {n: os.urandom(4000 + n) for n in range(1, 6)}
    drives_ = {"MASTER": (vols / "BACKUP", [1]), "WORKING": (vols / "WORK", [2]), "LEGACY": (vols / "OLD", [3])}
    for role, (root, nums) in drives_.items():
        (root / "Photos").mkdir(parents=True)
        for n in nums:
            (root / "Photos" / f"DSC_{n:04d}.NEF").write_bytes(data[n])
        code, lines = run(capsys, "volumes", "assign", str(root), "--role", role, "--label", root.name)
        assert code == 0, lines
        assert run(capsys, "index", str(root))[0] == 0
        assert run(capsys, "hash", str(root))[0] == 0
    card = vols / "CARD1"
    for folder, nums in (("100NZ6_2", [1, 2, 3, 4]), ("101NZ8", [5])):
        (card / "DCIM" / folder).mkdir(parents=True)
        for n in nums:
            (card / "DCIM" / folder / f"DSC_{n:04d}.NEF").write_bytes(data[n])
    return {"vols": vols, "card": card, "work": vols / "WORK"}


def listing(capsys, env) -> dict:
    code, lines = run(capsys, "cards", "list", "--root", str(env["vols"]))
    assert code == 0, lines
    return result(lines)


def tiers(env) -> dict[str, str]:
    c = db.connect(load_config().catalog_path)
    vid = c.execute("SELECT volume_id FROM volumes WHERE role = 'CARD'").fetchone()[0]
    return {Path(f["rel_path"]).name: f["tier"] for f in cards.card_files(c, vid, mount=env["card"])}


def test_worst_first() -> None:
    assert cards.worst(["backed_up", "needs_backup"]) == "needs_backup"
    assert cards.worst(["backed_up", "unchecked", "needs_backup"]) == "unchecked"
    assert cards.worst(["backed_up", "only_on_card", "unchecked"]) == "only_on_card"
    assert cards.worst(["backed_up"] * 3) == "backed_up"
    assert cards.worst([]) == "empty"


def test_new_card_is_unchecked_until_checked(env, capsys) -> None:
    [card] = listing(capsys, env)["cards"]
    assert card["tier"] == "unchecked" and card["counts"]["unchecked"]["files"] == 5 and card["mounted"]


def test_check_ranks_every_file_and_never_writes(env, capsys) -> None:
    before = tree_hashes(env["card"])
    code, lines = run(capsys, "cards", "check", str(env["card"]))
    assert code == 0, lines
    assert tree_hashes(env["card"]) == before  # read-only (vault-rules 1)
    assert result(lines)["tier"] == "only_on_card"
    assert tiers(env) == {"DSC_0001.NEF": "backed_up", "DSC_0002.NEF": "needs_backup",
                          "DSC_0003.NEF": "needs_backup",  # a LEGACY copy is yellow (D-046)
                          "DSC_0004.NEF": "only_on_card", "DSC_0005.NEF": "only_on_card"}

    out = listing(capsys, env)
    [card] = out["cards"]
    assert card["tier"] == "only_on_card"
    assert {t: card["counts"][t]["files"] for t in cards.TIERS} == {
        "only_on_card": 2, "unchecked": 0, "needs_backup": 2, "backed_up": 1}
    cams = {c["name"]: c for c in out["cameras"]}
    assert len(cams) == 2
    z8 = next(c for n, c in cams.items() if "8" in n)
    z6 = next(c for n, c in cams.items() if "8" not in n)
    assert (z8["tier"], z8["files"]) == ("only_on_card", 1)
    assert (z6["tier"], z6["files"]) == ("only_on_card", 4)  # worst of green, yellow, yellow, red


def test_card_turns_green_when_every_file_is_on_master(env, capsys) -> None:
    run(capsys, "cards", "check", str(env["card"]))
    master = env["vols"] / "BACKUP" / "Photos"
    for f in sorted(env["card"].rglob("*.NEF")):
        if not (master / f.name).exists():
            shutil.copy2(f, master / f.name)
    run(capsys, "index", str(env["vols"] / "BACKUP"))
    run(capsys, "hash", str(env["vols"] / "BACKUP"))
    run(capsys, "cards", "check", str(env["card"]))  # card-4: files with no copy were not read; re-check links them
    [card] = listing(capsys, env)["cards"]
    assert card["tier"] == "backed_up"
    assert {c["tier"] for c in listing(capsys, env)["cameras"]} == {"backed_up"}


def test_two_working_drives_are_green_and_legacy_never_counts(env, capsys) -> None:
    """D-089: verified on two different WORKING/MASTER drives = green; a LEGACY copy is never the second one."""
    run(capsys, "cards", "check", str(env["card"]))
    work2 = env["vols"] / "WORK2"
    (work2 / "Photos").mkdir(parents=True)
    src = env["card"] / "DCIM" / "100NZ6_2"
    for n in (2, 3):  # 2 is already on WORKING, 3 only on LEGACY
        shutil.copy2(src / f"DSC_{n:04d}.NEF", work2 / "Photos" / f"DSC_{n:04d}.NEF")
    assert run(capsys, "volumes", "assign", str(work2), "--role", "WORKING", "--label", "WORK2")[0] == 0
    run(capsys, "index", str(work2))
    run(capsys, "hash", str(work2))
    t = tiers(env)
    assert t["DSC_0002.NEF"] == "backed_up"  # WORK + WORK2
    assert t["DSC_0003.NEF"] == "needs_backup"  # WORK2 + OLD (LEGACY)
    c = db.connect(load_config().catalog_path)
    vid = c.execute("SELECT volume_id FROM volumes WHERE role = 'CARD'").fetchone()[0]
    reasons = {Path(f["rel_path"]).name: f["reason"] for f in cards.card_files(c, vid, mount=env["card"])}
    assert "WORK" in reasons["DSC_0002.NEF"] and "WORK2" in reasons["DSC_0002.NEF"]
    assert "second" in reasons["DSC_0003.NEF"]


def test_index_only_ranks_red_without_a_full_read(env, capsys) -> None:
    """After the index (partial hashes) but before the hash: no candidate means red; a candidate means unchecked."""
    c = db.connect(load_config().catalog_path)
    with repo.transaction(c):
        volumes.register_volume(c, volumes.probe(env["card"]))
    c.close()
    assert run(capsys, "index", str(env["card"]))[0] == 0
    assert tiers(env) == {"DSC_0001.NEF": "unchecked", "DSC_0002.NEF": "unchecked", "DSC_0003.NEF": "unchecked",
                          "DSC_0004.NEF": "only_on_card", "DSC_0005.NEF": "only_on_card"}


def test_new_or_changed_files_after_a_check_are_unchecked(env, capsys) -> None:
    run(capsys, "cards", "check", str(env["card"]))
    (env["card"] / "DCIM" / "100NZ6_2" / "DSC_0009.NEF").write_bytes(os.urandom(3000))
    f = env["card"] / "DCIM" / "100NZ6_2" / "DSC_0001.NEF"
    f.write_bytes(os.urandom(1234))
    t = tiers(env)
    assert t["DSC_0009.NEF"] == "unchecked" and t["DSC_0001.NEF"] == "unchecked"


def test_check_refuses_a_drive_that_is_not_a_card(env, capsys) -> None:
    code, lines = run(capsys, "cards", "check", str(env["work"]))
    assert code != 0 and any(e["event"] == "error" for e in lines)


def test_import_after_a_check_uses_the_naming_spec(env, capsys) -> None:
    """The check creates groups the way `index` does; ing-12 renames them at import."""
    run(capsys, "cards", "check", str(env["card"]))
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]))
    assert code == 0, lines
    imported = [p.name for p in (env["work"]).rglob("*.NEF") if p.parent.name != "Photos"]
    assert imported and all(n.startswith("M000") for n in imported), imported
    assert tiers(env)["DSC_0004.NEF"] == "needs_backup"  # now on WORKING


def test_check_full_reads_only_candidate_files(env, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    import photovault.index as index_mod

    real, seen = index_mod.sha256_file, []
    monkeypatch.setattr(index_mod, "sha256_file", lambda p, *a, **k: (seen.append(Path(p).name), real(p, *a, **k))[1])
    code, lines = run(capsys, "cards", "check", str(env["card"]))
    assert code == 0, lines
    assert "DSC_0004.NEF" not in seen and "DSC_0005.NEF" not in seen  # no copy anywhere: not read in full
    assert {"DSC_0001.NEF", "DSC_0002.NEF", "DSC_0003.NEF"} <= set(seen)
    assert result(lines)["no_candidate"] == 2
    assert tiers(env)["DSC_0004.NEF"] == "only_on_card"


def test_hash_on_a_normal_drive_still_hashes_everything(env, capsys, monkeypatch: pytest.MonkeyPatch) -> None:
    import photovault.index as index_mod

    real, seen = index_mod.sha256_file, []
    monkeypatch.setattr(index_mod, "sha256_file", lambda p, *a, **k: (seen.append(Path(p).name), real(p, *a, **k))[1])
    drive = env["vols"] / "WORK"
    for n in (7, 8):
        (drive / "Photos" / f"NEW_{n}.NEF").write_bytes(os.urandom(5000 + n))  # no candidate anywhere
    run(capsys, "index", str(drive))
    run(capsys, "hash", str(drive))
    assert {"NEW_7.NEF", "NEW_8.NEF"} <= set(seen)
