"""Lightroom catalog reader (idx-3) on a synthetic lrcat in tmp_path. Never touches /Volumes or a real catalog."""

from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path

import pytest

from photovault import lrcat, metadata, volumes
from photovault.catalog import db, repo
from photovault.config import load_config
from tests.test_cli import fake_probe, fake_read, result, run

MOUNT = "/Volumes/LEGACY1"


def make_lrcat(path: Path, files: list[tuple[str, str, str, str, str | None]],
               roots: tuple[str, ...] = (MOUNT + "/", "/Volumes/OTHER/")) -> Path:
    """files: (root index, pathFromRoot, baseName, extension, sidecarExtensions)."""
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE AgLibraryRootFolder (id_local INTEGER PRIMARY KEY, absolutePath TEXT);
        CREATE TABLE AgLibraryFolder (id_local INTEGER PRIMARY KEY, rootFolder INTEGER, pathFromRoot TEXT);
        CREATE TABLE AgLibraryFile (id_local INTEGER PRIMARY KEY, folder INTEGER, baseName TEXT, extension TEXT,
                                    sidecarExtensions TEXT);
    """)
    for i, r in enumerate(roots, 1):
        c.execute("INSERT INTO AgLibraryRootFolder VALUES (?, ?)", (i, r))
    folders: dict[tuple[str, str], int] = {}
    for root_i, folder, base, ext, side in files:
        key = (root_i, folder)
        if key not in folders:
            folders[key] = len(folders) + 1
            c.execute("INSERT INTO AgLibraryFolder VALUES (?, ?, ?)", (folders[key], int(root_i), folder))
        c.execute("INSERT INTO AgLibraryFile (folder, baseName, extension, sidecarExtensions) VALUES (?, ?, ?, ?)",
                  (folders[key], base, ext, side))
    c.commit()
    c.close()
    return path


FILES = [
    ("1", "2024/Trip A/", "DSC_0001", "NEF", "xmp"),
    ("1", "2024/Trip A/", "DSC_0001", "JPG", None),
    ("1", "2025/Trip B/", "DSC_0002", "NEF", "xmp,acr"),
    ("1", "", "root_file", "dng", ""),
    ("2", "x/", "elsewhere", "NEF", None),
]


def never_running() -> bool:
    return False


def test_referenced_paths_and_sidecars(tmp_path: Path) -> None:
    cat = make_lrcat(tmp_path / "Catalog.lrcat", FILES)
    before = cat.read_bytes()
    paths = lrcat.referenced_paths(cat, is_running=never_running)
    assert paths == {
        f"{MOUNT}/2024/Trip A/DSC_0001.NEF", f"{MOUNT}/2024/Trip A/DSC_0001.xmp", f"{MOUNT}/2024/Trip A/DSC_0001.JPG",
        f"{MOUNT}/2025/Trip B/DSC_0002.NEF", f"{MOUNT}/2025/Trip B/DSC_0002.xmp", f"{MOUNT}/2025/Trip B/DSC_0002.acr",
        f"{MOUNT}/root_file.dng", "/Volumes/OTHER/x/elsewhere.NEF",
    }
    assert cat.read_bytes() == before  # opened read-only: not a byte changed, no journal beside it
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Catalog.lrcat"]


def test_lock_file_refuses(tmp_path: Path) -> None:
    cat = make_lrcat(tmp_path / "Catalog.lrcat", FILES)
    (tmp_path / "Catalog.lrcat.lock").write_text("")
    with pytest.raises(lrcat.CatalogInUse, match="lock"):
        lrcat.referenced_paths(cat, is_running=never_running)


def test_running_lightroom_refuses(tmp_path: Path) -> None:
    cat = make_lrcat(tmp_path / "Catalog.lrcat", FILES)
    with pytest.raises(lrcat.CatalogInUse, match="running"):
        lrcat.referenced_paths(cat, is_running=lambda: True)


def test_lightroom_running_uses_exact_process_name() -> None:
    calls: list[list[str]] = []

    def fake_run(cmd, **_):  # noqa: ANN001
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if cmd[-1] == "Adobe Lightroom Classic" else 1)

    assert lrcat.lightroom_running(fake_run) is True
    assert calls[0] == ["pgrep", "-x", "Adobe Lightroom Classic"]
    assert lrcat.lightroom_running(lambda cmd, **_: subprocess.CompletedProcess(cmd, 1)) is False


def test_missing_tables_and_non_sqlite_report_schema_error(tmp_path: Path) -> None:
    empty = tmp_path / "Empty.lrcat"
    sqlite3.connect(empty).close()
    with pytest.raises(lrcat.LrcatSchemaError, match="AgLibraryFile"):
        lrcat.referenced_paths(empty, is_running=never_running)
    junk = tmp_path / "Junk.lrcat"
    junk.write_bytes(b"this is not sqlite" * 100)
    with pytest.raises(lrcat.LrcatSchemaError):
        lrcat.referenced_paths(junk, is_running=never_running)
    with pytest.raises(FileNotFoundError):
        lrcat.referenced_paths(tmp_path / "nope.lrcat", is_running=never_running)


def test_path_with_special_characters(tmp_path: Path) -> None:
    d = tmp_path / "My Lightroom #1 100%"
    d.mkdir()
    cat = make_lrcat(d / "Cat alog.lrcat", FILES)
    assert len(lrcat.referenced_paths(cat, is_running=never_running)) == 8


def _instance(c: sqlite3.Connection, vid: str, rel: str) -> None:
    c.execute("INSERT INTO instances (volume_id, rel_path, rel_path_key, size, mtime_ns, kind, state, last_seen_at) "
              "VALUES (?, ?, ?, 1, 1, 'raw', 'indexed', 'now')", (vid, rel, repo.path_key(rel)))


def test_mark_lr_referenced_matches_case_insensitively_and_only_this_volume(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    c = db.connect(tmp_path / "c.db")
    db.migrate(c)
    for vid in ("V1", "V2"):
        repo.upsert_volume(c, volume_id=vid, role="LEGACY", label=vid, fs_type="exfat", da_volume_uuid=None,
                           marker_id=None, capacity_bytes=None, mount_path=None)
    _instance(c, "V1", "2024/Trip A/dsc_0001.nef")  # on disk in lower case; Lightroom says NEF
    _instance(c, "V1", "2024/Trip A/DSC_0001.xmp")
    _instance(c, "V1", "2024/Trip A/DSC_0099.NEF")  # not in the catalog
    _instance(c, "V2", "2024/Trip A/DSC_0001.NEF")  # same relative path on another volume
    paths = {f"{MOUNT}/2024/Trip A/DSC_0001.NEF", f"{MOUNT}/2024/Trip A/DSC_0001.xmp", "/Volumes/OTHER/x/y.NEF"}
    assert lrcat.mark_lr_referenced(c, "V1", Path(MOUNT), paths) == 2
    assert lrcat.mark_lr_referenced(c, "V1", Path(MOUNT), paths) == 0  # idempotent
    flags = {(r["volume_id"], r["rel_path"]): r["lr_referenced"] for r in c.execute("SELECT * FROM instances")}
    assert flags == {("V1", "2024/Trip A/dsc_0001.nef"): 1, ("V1", "2024/Trip A/DSC_0001.xmp"): 1,
                     ("V1", "2024/Trip A/DSC_0099.NEF"): 0, ("V2", "2024/Trip A/DSC_0001.NEF"): 0}


def test_cli_lrcat_mark(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", fake_read)
    monkeypatch.setattr(lrcat, "lightroom_running", never_running)
    drive = tmp_path / "Volumes" / "LEGACY1"
    (drive / "2024/Trip A").mkdir(parents=True)
    (drive / "2024/Trip A/DSC_0001.NEF").write_bytes(b"a" * 100)
    (drive / "2024/Trip A/DSC_0001.xmp").write_text("<x/>")
    (drive / "2024/Trip A/DSC_0002.NEF").write_bytes(b"b" * 100)
    assert run(capsys, "volumes", "assign", str(drive), "--role", "LEGACY", "--label", "OLD")[0] == 0
    assert run(capsys, "index", str(drive))[0] == 0
    cat = make_lrcat(tmp_path / "Catalog.lrcat", [("1", "2024/Trip A/", "DSC_0001", "NEF", "xmp")],
                     roots=(str(drive) + "/",))
    code, lines = run(capsys, "lrcat", "mark", str(cat), "--volume", str(drive))
    assert code == 0, lines
    r = result(lines)
    assert r["referenced"] == 2 and r["on_volume"] == 2 and r["marked"] == 2
    c = db.connect(load_config().catalog_path)
    assert {x["rel_path"]: x["lr_referenced"] for x in c.execute("SELECT rel_path, lr_referenced FROM instances")} == {
        "2024/Trip A/DSC_0001.NEF": 1, "2024/Trip A/DSC_0001.xmp": 1, "2024/Trip A/DSC_0002.NEF": 0}
    # a held lock is reported as an error, not a crash, and marks nothing new
    (tmp_path / "Catalog.lrcat.lock").write_text("")
    code, lines = run(capsys, "lrcat", "mark", str(cat), "--volume", str(drive))
    assert code == 1 and lines[-1]["event"] == "error" and lines[-1]["code"] == "CatalogInUse"
