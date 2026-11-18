"""Metadata completion (ui-3): scan, refs, sidecars and the `meta` CLI, on tmp_path drives. Never /Volumes."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from photovault import meta, metadata, volumes
from photovault.catalog import db, repo
from photovault.config import load_config
from photovault.protocol import Emitter
from tests.test_cli import fake_probe, fake_read, result, run

GPS_FILE = "DSC_0001.NEF"


def read_with_gps(paths, **kw):  # noqa: ANN001
    """fake_read, plus a camera GPS fix on DSC_0001.NEF only."""
    out = fake_read(paths, **kw)
    for p in paths:
        if p.name == GPS_FILE and p in out.meta:
            out.meta[p].update(GPSLatitude=51.5, GPSLongitude=-0.12, GPSAltitude=35.0)
    return out


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> Path:
    """A WORKING drive with two groups: DSC_0001 (NEF + JPG) and DSC_0002 (JPG only), indexed and hashed."""
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", read_with_gps)
    mount = tmp_path / "Volumes" / "WORK"
    (mount / "Photos/2026").mkdir(parents=True)
    (mount / "Photos/2026/DSC_0001.NEF").write_bytes(os.urandom(4000))
    (mount / "Photos/2026/DSC_0001.JPG").write_bytes(os.urandom(900))
    (mount / "Photos/2026/DSC_0002.JPG").write_bytes(os.urandom(900))
    assert run(capsys, "volumes", "assign", str(mount), "--role", "WORKING", "--label", "WORK")[0] == 0
    assert run(capsys, "index", str(mount))[0] == 0
    assert run(capsys, "hash", str(mount))[0] == 0
    return mount


def conn():
    c = db.connect(load_config().catalog_path)
    db.migrate(c)
    return c


def group_ids(c) -> list[int]:
    return [r[0] for r in c.execute("SELECT group_id FROM capture_groups ORDER BY shutter_count")]


def events(lines: list[dict], name: str, **match) -> list[dict]:
    return [e for e in lines if e["event"] == name and all(e.get(k) == v for k, v in match.items())]


def test_missing_scan_and_rescan(work, capsys) -> None:
    code, lines = run(capsys, "meta", "missing", "--volume", str(work))
    assert code == 0, lines
    assert {k: result(lines)[k] for k in ("groups", "gps", "creator", "keywords")} == \
        {"groups": 2, "gps": 2, "creator": 2, "keywords": 2}

    code, lines = run(capsys, "meta", "scan", "--volume", str(work))
    assert code == 0, lines
    r = result(lines)
    assert (r["scanned"], r["with_gps"], r["errors"]) == (2, 1, 0)

    c = conn()
    g1, g2 = group_ids(c)
    cam = c.execute("SELECT cam_lat, lat, gps_source FROM group_meta WHERE group_id = ?", (g1,)).fetchone()
    assert (cam["cam_lat"], cam["lat"], cam["gps_source"]) == (51.5, 51.5, "camera")
    assert c.execute("SELECT lat FROM group_meta WHERE group_id = ?", (g2,)).fetchone()["lat"] is None
    point = c.execute("SELECT utc_time, source, source_ref, alt FROM geo_points").fetchone()
    # DSC_0001 is the second file (n=1 in fake_read): 21:00:02 naive, no offset, so the default +00:00 applies
    assert (point["utc_time"], point["source"], point["source_ref"], point["alt"]) == \
        ("2026-11-10T21:00:02+00:00", "catalog", f"group:{g1}", 35.0)

    assert run(capsys, "meta", "missing", "--volume", str(work))[1][-1]["gps"] == 1
    code, lines = run(capsys, "meta", "scan", "--volume", str(work))
    assert result(lines)["scanned"] == 0  # already read; --rescan re-reads them
    assert result(run(capsys, "meta", "scan", "--volume", str(work), "--rescan")[1])["scanned"] == 2


def test_set_fields_and_validation(work, capsys) -> None:
    c = conn()
    g1, g2 = group_ids(c)
    code, lines = run(capsys, "meta", "set", "--group", str(g1), str(g2), "--creator", "Mush",
                      "--copyright", "(c) 2026 Mush", "--title", "Harbour", "--keyword-add", "Travel",
                      "--keyword-add", "night", "--time-shift", "-60")
    assert code == 0, lines
    assert result(lines)["updated"] == 2
    row = c.execute("SELECT creator, copyright, title, keywords_json, time_shift_s, dirty FROM group_meta "
                    "WHERE group_id = ?", (g1,)).fetchone()
    assert (row["creator"], row["copyright"], row["title"], row["time_shift_s"], row["dirty"]) == \
        ("Mush", "(c) 2026 Mush", "Harbour", -60, 1)
    assert row["keywords_json"] == '["night", "Travel"]'

    code, lines = run(capsys, "meta", "set", "--group", str(g1), "--lat", "1.5", "--lon", "2.5", "--place", "Paris")
    assert code == 0, lines
    row = c.execute("SELECT lat, lon, alt, gps_source, place_name FROM group_meta WHERE group_id = ?", (g1,)).fetchone()
    assert (row["lat"], row["lon"], row["gps_source"], row["place_name"]) == (1.5, 2.5, "map", "Paris")

    assert run(capsys, "meta", "set", "--group", str(g1))[0] == 2  # nothing to set
    assert run(capsys, "meta", "set", "--group", str(g1), "--lat", "1.0")[0] == 2  # lat without lon
    code, lines = run(capsys, "meta", "missing", "--volume", str(work))
    assert result(lines)["creator"] == 0  # both groups now have a creator
    assert result(lines)["gps"] == 1  # the group given a manual geotag is no longer missing one


def test_refs_store_fixes_with_offsets(work, capsys, tmp_path, monkeypatch) -> None:
    folder = tmp_path / "Phone"
    folder.mkdir()
    for name in ("IMG_1.JPG", "IMG_2.JPG", "IMG_3.JPG", "IMG_1.xmp"):
        (folder / name).write_bytes(b"x" * 500)
    naive = datetime(2026, 11, 10, 10, 0, 0)

    def phone_read(paths, **kw):  # noqa: ANN001
        out = {}
        for p in paths:
            if p.name == "IMG_1.JPG":
                out[p] = {"GPSLatitude": 48.85, "GPSLongitude": 2.35, "DateTimeOriginal": naive,
                          "OffsetTimeOriginal": "+02:00", "CaptureTimeSource": "exif"}
            elif p.name == "IMG_2.JPG":
                out[p] = {"DateTimeOriginal": naive, "OffsetTimeOriginal": "+02:00"}
            elif p.name == "IMG_3.JPG":  # no offset in the file: the default_offset setting applies
                out[p] = {"GPSLatitude": 40.7, "GPSLongitude": -74.0, "DateTimeOriginal": naive}
        return metadata.MetadataResult(meta=out, errors={})

    monkeypatch.setattr(metadata, "read_metadata", phone_read)
    c = conn()
    repo.set_setting(c, "default_offset", "-05:00")
    c.close()

    code, lines = run(capsys, "meta", "refs", "--folder", str(folder), "--label", "phone")
    assert code == 0, lines
    r = result(lines)
    assert (r["files"], r["with_gps"], r["stored"], r["errors"]) == (3, 2, 2, 0)
    c = conn()
    rows = {p["source_ref"]: p["utc_time"] for p in c.execute("SELECT * FROM geo_points WHERE source = 'phone'")}
    assert rows == {"IMG_1.JPG": "2026-11-10T08:00:00+00:00", "IMG_3.JPG": "2026-11-10T15:00:00+00:00"}


def test_sidecars_write_then_conflict(work, capsys) -> None:
    c = conn()
    g1, g2 = group_ids(c)
    assert run(capsys, "meta", "scan", "--volume", str(work))[0] == 0
    assert run(capsys, "meta", "set", "--group", str(g1), str(g2), "--creator", "Mush")[0] == 0
    sidecar = work / "Photos/2026/DSC_0001.xmp"

    code, lines = run(capsys, "meta", "sidecars", "--volume", str(work), "--dry-run")
    assert code == 0, lines
    assert (result(lines)["would_write"], result(lines)["catalog_only"]) == (1, 1)
    assert not sidecar.exists()

    code, lines = run(capsys, "meta", "sidecars", "--volume", str(work))
    assert code == 0, lines
    r = result(lines)
    assert (r["written"], r["catalog_only"], r["conflicts"]) == (1, 1, 0)
    text = sidecar.read_text()
    assert "Mush" in text and "51,30.000000N" in text  # creator and camera GPS made it into the XMP
    assert not (work / "Photos/2026/DSC_0002.xmp").exists()  # JPEG-only group: catalog only
    assert c.execute("SELECT dirty FROM group_meta WHERE group_id = ?", (g1,)).fetchone()["dirty"] == 0
    assert c.execute("SELECT dirty FROM group_meta WHERE group_id = ?", (g2,)).fetchone()["dirty"] == 1
    rows = c.execute("SELECT rel_path, group_id FROM sidecars").fetchall()
    assert [(r["rel_path"], r["group_id"]) for r in rows] == [("Photos/2026/DSC_0001.xmp", g1)]

    # someone else (Lightroom, say) edits the sidecar; PhotoVault must not overwrite it
    sidecar.write_text("<x:xmpmeta>foreign</x:xmpmeta>")
    assert run(capsys, "meta", "set", "--group", str(g1), "--creator", "Other")[0] == 0
    code, lines = run(capsys, "meta", "sidecars", "--volume", str(work))
    assert code == 0, lines
    assert (result(lines)["conflicts"], result(lines)["written"]) == (1, 0)
    assert events(lines, "warning", code="sidecar_conflict")
    assert sidecar.read_text() == "<x:xmpmeta>foreign</x:xmpmeta>"
    assert c.execute("SELECT dirty FROM group_meta WHERE group_id = ?", (g1,)).fetchone()["dirty"] == 1


def test_sidecars_refused_on_legacy(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", read_with_gps)
    old = tmp_path / "Volumes" / "OLD"
    (old / "Photos").mkdir(parents=True)
    (old / "Photos" / "DSC_0001.NEF").write_bytes(os.urandom(3000))
    assert run(capsys, "volumes", "assign", str(old), "--role", "LEGACY", "--label", "OLD")[0] == 0
    assert run(capsys, "index", str(old))[0] == 0
    code, lines = run(capsys, "meta", "sidecars", "--volume", str(old))
    assert code == 1
    err = events(lines, "error")[-1]
    assert "WORKING" in err["message"] and "LEGACY" in err["message"]
    assert not (old / "Photos" / "DSC_0001.xmp").exists()


def test_scan_volume_direct_emits_progress(work, tmp_path) -> None:
    import io

    from photovault.config import load_config as lc

    c = conn()
    out = io.StringIO()
    counts = meta.scan_volume(c, lc(), work, c.execute("SELECT volume_id FROM volumes").fetchone()[0],
                              Emitter(out), rescan=True)
    assert counts == {"scanned": 2, "with_gps": 1, "errors": 0}
    assert '"event": "progress"' in out.getvalue()
