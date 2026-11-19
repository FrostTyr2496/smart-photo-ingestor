"""Safe card clear (cc-1, D-092). tmp_path drives only; never /Volumes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from photovault import card_clear, metadata, volumes
from photovault.catalog import db, repo
from photovault.config import load_config
from tests.test_cli import fake_probe, fake_read, result, run, tree_hashes


def build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> dict:
    """Card frames (all under DCIM unless noted):
    1 NEF  on WORK + WORK2            -> green
    2 NEF+JPG both on WORK + WORK2    -> green group
    3 NEF  on WORK only               -> yellow
    4 NEF  nowhere else               -> red
    5 NEF  on WORK + LEGACY           -> yellow (LEGACY never counts)
    6 NEF on WORK + WORK2, JPG on WORK only -> group kept whole
    7 NEF  green, but under MISC/     -> outside DCIM
    """
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", fake_read)
    vols = tmp_path / "Vols"
    data = {n: os.urandom(4000 + n) for n in range(1, 10)}
    jpg = {2: os.urandom(777), 6: os.urandom(888)}
    layout = {
        "WORK": ("WORKING", {f"DSC_{n:04d}.NEF": data[n] for n in (1, 2, 3, 5, 6, 7)} | {"DSC_0002.JPG": jpg[2], "DSC_0006.JPG": jpg[6]}),
        "WORK2": ("WORKING", {f"DSC_{n:04d}.NEF": data[n] for n in (1, 2, 6, 7)} | {"DSC_0002.JPG": jpg[2]}),
        "OLD": ("LEGACY", {"DSC_0005.NEF": data[5]}),
    }
    for name, (role, files) in layout.items():
        root = vols / name
        (root / "Photos").mkdir(parents=True)
        for fname, blob in files.items():
            (root / "Photos" / fname).write_bytes(blob)
        assert run(capsys, "volumes", "assign", str(root), "--role", role, "--label", name)[0] == 0
        assert run(capsys, "index", str(root))[0] == 0
        assert run(capsys, "hash", str(root))[0] == 0
    card = vols / "CARD1"
    (card / "DCIM" / "100NZ6_2").mkdir(parents=True)
    (card / "MISC").mkdir()
    for n in (1, 2, 3, 4, 5, 6):
        (card / "DCIM" / "100NZ6_2" / f"DSC_{n:04d}.NEF").write_bytes(data[n])
    for n in (2, 6):
        (card / "DCIM" / "100NZ6_2" / f"DSC_{n:04d}.JPG").write_bytes(jpg[n])
    (card / "MISC" / "DSC_0007.NEF").write_bytes(data[7])
    code, lines = run(capsys, "cards", "check", str(card))
    assert code == 0, lines
    return {"vols": vols, "card": card, "data": data}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> dict:
    return build(tmp_path, monkeypatch, capsys)


def dcim(env: dict) -> Path:
    return env["card"] / "DCIM" / "100NZ6_2"


def names(env: dict) -> set[str]:
    return {p.name for p in env["card"].rglob("*") if p.is_file()}


def clear(capsys, env: dict, *extra: str):
    return run(capsys, "cards", "clear", str(env["card"]), "--root", str(env["vols"]), *extra)


def conn_() -> object:
    return db.connect(load_config().catalog_path)


def test_dry_run_plans_and_deletes_nothing(env, capsys) -> None:
    before = tree_hashes(env["card"])
    code, lines = clear(capsys, env, "--dry-run")
    assert code == 0, lines
    plan = result(lines)
    assert plan["deletable_files"] == 3 and plan["groups"] == 2  # frame 1 and frame 2 (NEF + JPG)
    assert plan["kept"]["needs_backup"]["files"] == 3  # frames 3 and 5, and frame 6's JPG
    assert plan["kept"]["only_on_card"]["files"] == 1
    assert plan["kept"]["group_not_green"]["files"] == 1  # frame 6's NEF, held back by its JPG
    assert plan["kept"]["outside_dcim"]["files"] == 1
    assert sorted(plan["copy_drives"]) == ["WORK", "WORK2"]
    assert tree_hashes(env["card"]) == before
    assert conn_().execute("SELECT COUNT(*) FROM jobs WHERE kind = 'card_clear'").fetchone()[0] == 0


def test_clear_deletes_exactly_the_green_groups(env, capsys) -> None:
    work_before = tree_hashes(env["vols"] / "WORK") | {f"2/{k}": v for k, v in tree_hashes(env["vols"] / "WORK2").items()}
    code, lines = clear(capsys, env, "--confirm", "3")
    assert code == 0, lines
    r = result(lines)
    assert (r["deleted"], r["stopped"]) == (3, None)
    assert names(env) == {"DSC_0003.NEF", "DSC_0004.NEF", "DSC_0005.NEF", "DSC_0006.NEF", "DSC_0006.JPG",
                          "DSC_0007.NEF"} | (names(env) & {"NIKON001.DSC"})
    assert (env["card"] / "MISC" / "DSC_0007.NEF").exists()  # nothing outside DCIM
    assert work_before == tree_hashes(env["vols"] / "WORK") | {f"2/{k}": v for k, v in tree_hashes(env["vols"] / "WORK2").items()}
    c = conn_()
    states = {r["rel_path"]: r["state"] for r in c.execute(
        "SELECT i.rel_path, i.state FROM instances i JOIN volumes v USING (volume_id) WHERE v.role = 'CARD'")}
    assert states["DCIM/100NZ6_2/DSC_0001.NEF"] == "missing" and states["DCIM/100NZ6_2/DSC_0003.NEF"] == "verified"
    job = c.execute("SELECT * FROM jobs WHERE kind = 'card_clear'").fetchone()
    assert job["state"] == "done" and job["finished_at"]
    items = c.execute("SELECT state FROM job_items WHERE job_id = ?", (job["job_id"],)).fetchall()
    assert [i["state"] for i in items] == ["committed"] * 3
    assert card_clear.cleared_at(c, job["source_volume_id"]) == job["finished_at"]
    # nothing left to clear: refused
    code, lines = clear(capsys, env, "--confirm", "0")
    assert code == 2 and "nothing can be deleted" in lines[-1]["message"]


def test_wrong_or_missing_confirm_is_refused(env, capsys) -> None:
    before = tree_hashes(env["card"])
    for n in ("2", "4", "0"):
        code, lines = clear(capsys, env, "--confirm", n)
        assert code == 2 and "does not match" in lines[-1]["message"]
    code, lines = clear(capsys, env)
    assert code == 2 and "--confirm" in lines[-1]["message"]
    assert tree_hashes(env["card"]) == before


def test_changed_card_file_stops_before_deleting_it(env, capsys) -> None:
    f = dcim(env) / "DSC_0002.NEF"
    st = f.stat()
    f.write_bytes(os.urandom(st.st_size))  # same size, other bytes
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns))
    code, lines = clear(capsys, env, "--confirm", "3")
    assert code == 1
    r = result(lines)
    assert r["stopped"] and "checksum" in r["stopped"]
    assert (dcim(env) / "DSC_0002.NEF").exists() and (dcim(env) / "DSC_0002.JPG").exists()  # the group stays whole
    assert not (dcim(env) / "DSC_0001.NEF").exists()  # the earlier, verified group was already deleted
    c = conn_()
    assert c.execute("SELECT state FROM jobs WHERE kind = 'card_clear'").fetchone()[0] == "failed"
    assert c.execute("SELECT COUNT(*) FROM job_items WHERE state = 'failed'").fetchone()[0] == 1


def test_size_change_after_check_is_not_even_planned(env, capsys) -> None:
    (dcim(env) / "DSC_0001.NEF").write_bytes(b"short")
    code, lines = clear(capsys, env, "--dry-run")
    assert result(lines)["deletable_files"] == 2 and result(lines)["kept"]["unchecked"]["files"] == 1


def test_no_copy_drive_connected_refuses(env, capsys) -> None:
    for name in ("WORK", "WORK2"):  # both copy drives unplugged
        shutil.rmtree(env["vols"] / name)
    code, lines = clear(capsys, env, "--dry-run")
    assert result(lines)["deletable_files"] == 0 and result(lines)["kept"]["copy_not_connected"]["files"] == 4
    before = tree_hashes(env["card"])
    code, lines = clear(capsys, env, "--confirm", "0")
    assert code == 2 and "nothing can be deleted" in lines[-1]["message"]
    assert tree_hashes(env["card"]) == before


def test_one_copy_drive_unplugged_still_needs_a_connected_copy(env, capsys) -> None:
    shutil.rmtree(env["vols"] / "WORK2")
    code, lines = clear(capsys, env, "--dry-run")
    assert result(lines)["deletable_files"] == 3  # the catalog still holds two copies; WORK is re-read
    code, lines = clear(capsys, env, "--confirm", "3")
    assert code == 0 and result(lines)["deleted"] == 3


def test_changed_copy_stops(env, capsys) -> None:
    for name in ("WORK", "WORK2"):
        p = env["vols"] / name / "Photos" / "DSC_0001.NEF"
        st = p.stat()
        p.write_bytes(os.urandom(st.st_size))
        os.utime(p, ns=(st.st_atime_ns, st.st_mtime_ns))
    code, lines = clear(capsys, env, "--confirm", "3")
    assert code == 1 and "does not match" in result(lines)["stopped"]
    assert (dcim(env) / "DSC_0001.NEF").exists()


def test_stop_flag_halts_between_groups(env, tmp_path, capsys) -> None:
    c = conn_()
    from photovault.protocol import Emitter
    out = card_clear.run_clear(c, load_config(), env["card"], confirm=3, volumes_root=env["vols"], emit=Emitter(),
                               should_stop=lambda: True)
    capsys.readouterr()
    assert out["deleted"] == 0 and out["stopped"] == "stopped by user"
    assert (dcim(env) / "DSC_0001.NEF").exists()


def test_only_cards_are_ever_cleared(env, capsys) -> None:
    work = env["vols"] / "WORK"
    (work / "DCIM").mkdir()  # a working drive that looks like a card is still not a CARD volume
    code, lines = run(capsys, "cards", "clear", str(work), "--root", str(env["vols"]), "--dry-run")
    assert code == 2 and "not CARD" in lines[-1]["message"]
    code, lines = run(capsys, "cards", "clear", str(env["vols"] / "OLD"), "--root", str(env["vols"]), "--dry-run")
    assert code == 2 and "not a camera card" in lines[-1]["message"]
    c = conn_()
    wrow = c.execute("SELECT * FROM volumes WHERE label = 'WORK'").fetchone()
    victim = work / "Photos" / "DSC_0001.NEF"
    with pytest.raises(card_clear.Surprise, match="not a CARD"):
        card_clear._delete(c, wrow, work, "DCIM/x.NEF")
    assert victim.exists()


def test_delete_guard_refuses_outside_dcim_and_symlinks(env) -> None:
    c = conn_()
    card = c.execute("SELECT * FROM volumes WHERE role = 'CARD'").fetchone()
    with pytest.raises(card_clear.Surprise, match="outside DCIM"):
        card_clear._delete(c, card, env["card"], "MISC/DSC_0007.NEF")
    link = dcim(env) / "LINK.NEF"
    link.symlink_to(env["card"] / "MISC" / "DSC_0007.NEF")
    with pytest.raises(card_clear.Surprise, match="outside DCIM"):
        card_clear._delete(c, card, env["card"], "DCIM/100NZ6_2/LINK.NEF")
    assert (env["card"] / "MISC" / "DSC_0007.NEF").exists()


def test_cards_list_reports_when_a_card_was_cleared(env, capsys) -> None:
    code, lines = run(capsys, "cards", "list", "--root", str(env["vols"]))
    assert result(lines)["cards"][0].get("cleared_at") is None
    clear(capsys, env, "--confirm", "3")
    from photovault import cards, drives
    c = conn_()
    [row] = cards.list_cards(c, drives.scan_mounts(c, env["vols"], __import__("photovault.protocol", fromlist=["Emitter"]).Emitter()))["cards"]
    assert row["cleared_at"]


def test_web_argv() -> None:
    from photovault.web.server import BadRequest, build_argv
    assert build_argv("card_clear", {"mount": "/m", "dry_run": True}) == ["cards", "clear", "/m", "--dry-run"]
    assert build_argv("card_clear", {"mount": "/m", "confirm": 12}) == ["cards", "clear", "/m", "--confirm", "12"]
    for bad in ({"mount": "/m"}, {"mount": "/m", "confirm": 0}, {"mount": "/m", "confirm": "3"},
                {"mount": "/m", "confirm": True}, {"mount": "/m", "force": True}):
        with pytest.raises(BadRequest):
            build_argv("card_clear", bad)
