"""End-to-end: the CLI on tmp_path "cards" and "drives" (fnd-6, ing-6..9). Never touches /Volumes."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import photovault.ingest as ingest_mod
from photovault import cli, metadata, volumes
from photovault.catalog import db, repo
from photovault.config import load_config
from photovault.metadata import MetadataResult
from photovault.volumes import VolumeProbe

REAL_PROBE = volumes.probe


def fake_probe(mount: Path, **_: object) -> VolumeProbe:
    mount = Path(mount)
    return VolumeProbe(mount, mount.name, "exfat", f"UUID-{mount.name}", 10**12, False,
                       volumes.read_marker(mount), volumes.looks_like_card(mount))


def fake_read(paths, **kw) -> MetadataResult:  # noqa: ANN001
    meta = {}
    for p in paths:
        m = re.search(r"(\d+)$", p.stem)
        n = int(m.group(1)) if m else 0
        model = "NIKON Z 8" if "Z8" in p.parent.name else "NIKON Z 6_2"
        meta[p] = {"Model": model, "CameraSerialNumber": "0000001", "ShutterCount": 1000 + n,
                   "DateTimeOriginal": datetime(2026, 11, 10, 21, 0) + timedelta(seconds=2 * n),
                   "SubSecTimeOriginal": "12", "CaptureTimeSource": "exif"}
    if kw.get("progress"):
        kw["progress"](len(paths), len(paths))
    return MetadataResult(meta=meta, errors={})


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Path]:
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", fake_read)
    vols = tmp_path / "Volumes"
    card = vols / "CARD1"
    d = card / "DCIM" / "100NZ6_2"
    d.mkdir(parents=True)
    for i in (1, 2, 3):
        (d / f"DSC_000{i}.NEF").write_bytes(os.urandom(300_000))
        (d / f"DSC_000{i}.JPG").write_bytes(os.urandom(50_000))
    (card / "NIKON001.DSC").write_bytes(b"ignored")
    work = vols / "WORK"
    work.mkdir()
    return {"vols": vols, "card": card, "work": work}


def run(capsys: pytest.CaptureFixture, *args: str) -> tuple[int, list[dict]]:
    code = cli.main(["--json", *args])
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    return code, lines


def result(lines: list[dict]) -> dict:
    return [e for e in lines if e["event"] == "result"][-1]


def tree_hashes(root: Path) -> dict[str, tuple[str, int]]:
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns)
            for p in sorted(root.rglob("*")) if p.is_file()}


def media_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.is_file() and not p.name.startswith("."))


def assign(capsys, env) -> None:
    code, lines = run(capsys, "volumes", "assign", str(env["work"]), "--role", "WORKING", "--label", "WORK")
    assert code == 0, lines
    assert (env["work"] / ".photovault-id").exists()


def test_full_card_flow(env, capsys) -> None:
    card_before = tree_hashes(env["card"])
    code, lines = run(capsys, "drives", "--root", str(env["vols"]))
    roles = {Path(r["mount"]).name: r["role"] for r in result(lines)["drives"]}
    assert roles == {"CARD1": "CARD (new)", "WORK": "unassigned"}

    assign(capsys, env)
    code, lines = run(capsys, "card-status", str(env["card"]))
    assert code == 3 and result(lines)["unknown"] == 6

    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]), "--name", "Night Test", "--dry-run")
    assert code == 0
    plan = result(lines)
    assert plan["files"] == 6 and plan["groups"] == 3
    assert plan["folders"] == [{"folder": "Photography/Imports/2026/20261110 - Night Test/Z6II", "files": 6}]
    assert media_files(env["work"]) == []
    code, lines = run(capsys, "jobs")
    assert result(lines)["jobs"] == []

    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]), "--name", "Night Test")
    assert code == 0, lines
    assert result(lines)["copied"] == 6
    names = [p.name for p in media_files(env["work"])]
    assert "M0001_20261110-210002-12_Z6II_0001001.NEF" in names and len(names) == 6
    assert not list(env["work"].rglob("*.pvpartial"))

    code, lines = run(capsys, "card-status", str(env["card"]))
    assert code == 0 and result(lines)["state"] == "green"

    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]), "--name", "Night Test")
    assert code == 0 and result(lines)["copied"] == 0 and result(lines)["skipped_dup"] == 6
    assert len(media_files(env["work"])) == 6
    assert tree_hashes(env["card"]) == card_before

    (env["card"] / "DCIM" / "100NZ6_2" / "DSC_0004.NEF").write_bytes(b"new shot" * 1000)
    code, lines = run(capsys, "card-status", str(env["card"]))
    assert code == 3 and result(lines)["unknown"] == 1


def test_duplicate_bytes_on_one_card_copied_once(env, capsys) -> None:
    d = env["card"] / "DCIM" / "100NZ6_2"
    (d / "DSC_0009.NEF").write_bytes((d / "DSC_0001.NEF").read_bytes())
    assign(capsys, env)
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]))
    assert result(lines)["copied"] == 6 and result(lines)["skipped_dup"] == 1
    code, _ = run(capsys, "card-status", str(env["card"]))
    assert code == 0


def test_stop_then_resume(env, capsys, monkeypatch) -> None:
    assign(capsys, env)
    calls = {"n": 0}

    def stop_after_3() -> bool:
        calls["n"] += 1
        return calls["n"] > 3

    real_run_job = cli.run_job
    monkeypatch.setattr(cli, "run_job", lambda *a, **k: real_run_job(*a, **{**k, "should_stop": stop_after_3}))
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]))
    assert code == 4
    job_id = result(lines)["job_id"] if any(e["event"] == "result" for e in lines) else 1
    assert len(media_files(env["work"])) == 3
    monkeypatch.setattr(cli, "run_job", real_run_job)
    code, lines = run(capsys, "resume", str(job_id))
    assert code == 0 and result(lines)["copied"] == 3
    assert len(media_files(env["work"])) == 6


def test_target_unplugged_pauses_then_resumes(env, capsys, monkeypatch) -> None:
    assign(capsys, env)
    real_copy = ingest_mod.copy_verified
    calls = {"n": 0}
    hidden = env["work"].with_name("WORK_unplugged")

    def flaky(src, dst, **kw):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 3:
            env["work"].rename(hidden)
            raise OSError("device not configured")
        return real_copy(src, dst, **kw)

    monkeypatch.setattr(ingest_mod, "copy_verified", flaky)
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]))
    assert code == 4
    hidden.rename(env["work"])
    code, lines = run(capsys, "resume", "1")
    assert code == 0
    assert len(media_files(env["work"])) == 6
    code, _ = run(capsys, "card-status", str(env["card"]))
    assert code == 0


def test_name_collision_never_overwrites(env, capsys) -> None:
    assign(capsys, env)
    target = env["work"] / "Photography/Imports/2026/20261110/Z6II/M0001_20261110-210002-12_Z6II_0001001.NEF"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"someone else's file")
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]))
    assert code == 1
    failed = [e for e in lines if e["event"] == "item" and e["state"] == "failed"]
    assert len(failed) == 1 and "name collision" in failed[0]["error"]
    assert target.read_bytes() == b"someone else's file"
    code, _ = run(capsys, "card-status", str(env["card"]))
    assert code == 3


def test_refusals(env, capsys) -> None:
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]))
    assert code == 1 and "volumes assign" in lines[-1]["message"]
    assign(capsys, env)
    code, lines = run(capsys, "ingest", "--source", str(env["work"]), "--target", str(env["work"]))
    assert code == 1
    code, lines = run(capsys, "volumes", "assign", str(env["card"]), "--role", "WORKING")
    assert code == 1 and not (env["card"] / ".photovault-id").exists()
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["card"]))
    assert code == 1


def test_unknown_body_is_named_and_warned(env, capsys) -> None:
    d = env["card"] / "DCIM" / "101Z8"
    d.mkdir()
    (d / "DSC_0005.NEF").write_bytes(os.urandom(1000))
    assign(capsys, env)
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]), "--dry-run")
    folders = [f["folder"] for f in result(lines)["folders"]]
    assert any(f.endswith("/Z8") for f in folders)


def test_video_without_model_inherits_folder_body(env, capsys, monkeypatch) -> None:
    d = env["card"] / "DCIM" / "100NZ6_2"
    (d / "DSC_0007.MOV").write_bytes(os.urandom(1000))

    def read(paths, **kw):  # noqa: ANN001
        res = fake_read(paths, **kw)
        for p in res.meta:
            if p.suffix == ".MOV":
                res.meta[p] = {k: v for k, v in res.meta[p].items() if k not in ("Model", "ShutterCount", "CameraSerialNumber")}
        return res

    monkeypatch.setattr(metadata, "read_metadata", read)
    assign(capsys, env)
    code, lines = run(capsys, "ingest", "--source", str(env["card"]), "--target", str(env["work"]), "--dry-run")
    r = result(lines)
    assert r["body_inferred"] == 1
    assert [f["folder"].rsplit("/", 1)[-1] for f in r["folders"]] == ["Z6II"]


def test_usage_error_exit_code(capsys) -> None:
    assert cli.main(["nonsense"]) == 2


def test_catalog_init(env, capsys) -> None:
    code, lines = run(capsys, "catalog", "init")
    assert code == 0 and result(lines)["version"] >= 1
