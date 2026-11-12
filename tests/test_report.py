"""Duplicate and coverage report (idx-4). Catalog rows are built in tmp_path; nothing touches /Volumes."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from photovault import metadata, report, volumes
from photovault.catalog import db, repo
from tests.test_baseline_hash import assign_and_index, make_drive
from tests.test_cli import fake_probe, fake_read, result, run, tree_hashes


@pytest.fixture
def cat(tmp_path: Path):
    c = db.connect(tmp_path / "cat.db")
    db.migrate(c)
    for vid, role, label in (("v-old", "LEGACY", "OLD"), ("v-work", "WORKING", "WORK"), ("v-card", "CARD", "CARD1")):
        repo.upsert_volume(c, volume_id=vid, role=role, label=label, fs_type="exfat", da_volume_uuid=f"U-{vid}",
                           marker_id=None, capacity_bytes=10**12, mount_path=f"/mnt/{label}")
    return c


def put(c, volume: str, rel: str, sha: str, size: int, *, state: str = "verified", lr: bool = False) -> None:
    aid = repo.insert_asset(c, sha256=sha * 64, size=size, partial_sha256=sha * 8, kind="raw") if state != "indexed" else None
    iid = repo.upsert_instance(c, volume_id=volume, rel_path=rel, size=size, mtime_ns=1, kind="raw", state=state,
                               asset_id=aid)
    if lr:
        c.execute("UPDATE instances SET lr_referenced = 1 WHERE instance_id = ?", (iid,))
    c.commit()


def test_clusters_count_verified_copies_on_non_card_volumes(cat) -> None:
    put(cat, "v-old", "a/1.NEF", "a", 1000, lr=True)
    put(cat, "v-old", "b/1_copy.NEF", "a", 1000)  # a duplicate inside one volume counts
    put(cat, "v-work", "2026/1.NEF", "a", 1000)
    put(cat, "v-old", "a/2.NEF", "b", 5000)
    put(cat, "v-card", "DCIM/2.NEF", "b", 5000)  # card + legacy is not a duplicate
    put(cat, "v-old", "a/3.NEF", "c", 700)
    put(cat, "v-work", "2026/3.NEF", "c", 700, state="missing")  # a missing file is not a copy
    put(cat, "v-old", "a/4.NEF", "d", 700)
    put(cat, "v-work", "2026/4.NEF", "d", 700, state="mismatch")  # neither is a mismatch

    clusters = report.duplicates(cat)

    assert len(clusters) == 1
    c = clusters[0]
    assert c.size == 1000 and len(c.instances) == 3 and c.reclaimable == 2000
    assert [(i.volume_label, i.rel_path, i.lr_referenced) for i in c.instances] == [
        ("OLD", "a/1.NEF", True), ("OLD", "b/1_copy.NEF", False), ("WORK", "2026/1.NEF", False)]


def test_clusters_sorted_by_reclaimable_and_volume_filter(cat) -> None:
    put(cat, "v-old", "s.NEF", "a", 100)
    put(cat, "v-work", "s.NEF", "a", 100)
    put(cat, "v-old", "big1.MOV", "b", 9000)
    put(cat, "v-old", "big2.MOV", "b", 9000)

    assert [c.size for c in report.duplicates(cat)] == [9000, 100]
    # only clusters with a copy on WORK; the other copies are still listed
    only_work = report.duplicates(cat, volume_id="v-work")
    assert [c.size for c in only_work] == [100]
    assert {i.volume_label for i in only_work[0].instances} == {"OLD", "WORK"}
    assert report.duplicates(cat, volume_id="v-card") == []


def test_coverage_counts_only_here_unhashed_and_mismatch(cat) -> None:
    put(cat, "v-old", "dup.NEF", "a", 100)
    put(cat, "v-work", "dup.NEF", "a", 100)
    put(cat, "v-old", "solo1.NEF", "b", 400)
    put(cat, "v-old", "solo1_again.NEF", "b", 400)  # same asset twice on one volume: one asset only here
    put(cat, "v-old", "unhashed.NEF", "c", 50, state="indexed")
    put(cat, "v-old", "gone.NEF", "d", 50, state="missing")
    put(cat, "v-work", "bad.NEF", "e", 60, state="mismatch")
    put(cat, "v-card", "DCIM/new.NEF", "f", 300)

    cov = {s.label: s for s in report.coverage(cat)}

    assert (cov["OLD"].files, cov["OLD"].unhashed, cov["OLD"].only_here_files, cov["OLD"].only_here_bytes) == (4, 1, 1, 400)
    assert (cov["WORK"].files, cov["WORK"].mismatch, cov["WORK"].only_here_files) == (2, 1, 0)
    assert (cov["CARD1"].only_here_files, cov["CARD1"].only_here_bytes) == (1, 300)  # not copied off the card yet


def test_write_markdown(cat, tmp_path: Path) -> None:
    put(cat, "v-old", "a|b/1.NEF", "a", 1_500_000, lr=True)
    put(cat, "v-work", "x/1.NEF", "a", 1_500_000)
    out = tmp_path / "deep" / "r.md"

    report.write_markdown(report.duplicates(cat), out, summaries=report.coverage(cat))

    text = out.read_text()
    assert "Duplicate clusters: 1 (2 files)" in text
    assert "Reclaimable: 1.5 MB (1500000 bytes)" in text
    assert "- OLD [LEGACY]: `a|b/1.NEF` (Lightroom)" in text
    assert "- WORK [WORKING]: `x/1.NEF`" in text
    assert "| OLD | LEGACY | 1 | 0 | 0 | 0 (0 B) |" in text
    assert ("a" * 12) in text and ("a" * 13) not in text  # a short hash, not the full one


def unreadable_file(c, volume: str, rel: str, size: int, partial: str, *, group: tuple[int, str] | None = None) -> None:
    """A `mismatch` instance with no asset, as `hash` leaves a file whose read failed (idx-11)."""
    repo.upsert_instance(c, volume_id=volume, rel_path=rel, size=size, mtime_ns=1, kind="raw", state="mismatch",
                         partial_sha256=partial)
    if group is not None:  # `index` records group membership as index job items
        job = repo.create_job(c, kind="index_volume", source_volume_id=volume, target_volume_id=None, params={})
        repo.add_job_items(c, job, [{"seq": 1, "src_rel": rel, "dst_rel": None, "group_id": group[0],
                                     "role": group[1]}])
    c.commit()


def frame(c, shutter: int) -> int:
    body = repo.get_or_create_body(c, "NIKON Z 6_2", "0000001", "Z6II")
    return repo.insert_group(c, body_id=body, shutter_count=shutter, capture_time=None, capture_offset=None,
                             time_source="exif", canonical_stem=f"DSC_{shutter}", original_stem=f"DSC_{shutter}",
                             original_num=shutter, shoot_id=None)


def test_unreadable_files_and_where_a_copy_may_be(cat) -> None:
    put(cat, "v-work", "2026/1.NEF", "a", 1000)  # same size + partial as the damaged 1.NEF
    put(cat, "v-old", "elsewhere/2.NEF", "b", 2000)  # same volume as the damaged 2.NEF: no rescue
    g3, g4 = frame(cat, 3), frame(cat, 4)
    aid = repo.insert_asset(cat, sha256="c" * 64, size=3100, partial_sha256="x" * 8, kind="raw", group_id=g3,
                            group_role="raw")
    repo.upsert_instance(cat, volume_id="v-work", rel_path="2026/3.NEF", size=3100, mtime_ns=1, kind="raw",
                         state="verified", asset_id=aid)
    aid = repo.insert_asset(cat, sha256="d" * 64, size=400, partial_sha256="y" * 8, kind="jpeg", group_id=g4,
                            group_role="jpeg")
    repo.upsert_instance(cat, volume_id="v-work", rel_path="2026/4.JPG", size=400, mtime_ns=1, kind="jpeg",
                         state="verified", asset_id=aid)
    unreadable_file(cat, "v-old", "bad/1.NEF", 1000, "a" * 8)
    unreadable_file(cat, "v-old", "bad/2.NEF", 2000, "b" * 8)
    unreadable_file(cat, "v-old", "bad/3.NEF", 3000, "z" * 8, group=(g3, "raw"))
    unreadable_file(cat, "v-old", "bad/4.NEF", 4000, "w" * 8, group=(g4, "raw"))  # only the JPG survives

    got = {u.rel_path: u.rescue for u in report.unreadable(cat)}

    assert got["bad/1.NEF"].startswith("likely copy on WORK") and "unconfirmed" in got["bad/1.NEF"]
    assert got["bad/2.NEF"] == report.NO_COPY
    assert got["bad/3.NEF"].startswith("same frame on WORK")
    assert got["bad/4.NEF"] == report.NO_COPY  # a JPG of the frame is no copy of the NEF
    assert len(got) == 4  # the coverage table's `mismatch` rows with an asset are not unreadable files


def test_write_markdown_lists_unreadable_files(cat, tmp_path: Path) -> None:
    put(cat, "v-work", "2026/1.NEF", "a", 1000)
    unreadable_file(cat, "v-old", "bad/1.NEF", 1000, "a" * 8)
    unreadable_file(cat, "v-old", "bad/9.NEF", 9000, "q" * 8)
    out = tmp_path / "r.md"

    report.write_markdown([], out, summaries=report.coverage(cat), unreadable=report.unreadable(cat))

    text = out.read_text()
    assert "## Unreadable files (I/O errors)" in text
    assert "2 files could not be read in full; 1 have no known copy" in text
    assert "| OLD | `bad/9.NEF` | 9.0 KB | no known copy |" in text
    assert "| OLD | LEGACY | 2 | 0 | 2 | 0 (0 B) |" in text
    report.write_markdown([], out, unreadable=[])
    assert "Unreadable files" not in out.read_text()


def test_write_markdown_empty_report(cat, tmp_path: Path) -> None:
    out = tmp_path / "r.md"
    report.write_markdown([], out)
    assert "No duplicates found." in out.read_text()


def test_report_refuses_a_destination_on_volumes(cat) -> None:
    target = Path("/Volumes/SomeDrive/reports/r.md")
    with pytest.raises(ValueError, match="/Volumes"):
        report.write_markdown([], target)
    assert not target.exists()


# --- CLI, end to end on two tmp "legacy drives" ------------------------------------------------------------------


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("PHOTOVAULT_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    monkeypatch.setattr(metadata, "read_metadata", fake_read)
    vols = tmp_path / "Volumes"
    a = make_drive(vols / "OLD1")
    b = vols / "OLD2"
    shutil.copytree(a, b)  # same bytes under the same names: every file is a duplicate
    (b / "Photos" / "only_b.NEF").write_bytes(os.urandom(4000))
    return {"a": a, "b": b}


def test_cli_report_duplicates(capsys, env, tmp_path: Path) -> None:
    for drive, label in ((env["a"], "OLDA"), (env["b"], "OLDB")):
        assign_and_index(capsys, drive, label)
        code, lines = run(capsys, "hash", str(drive))
        assert code == 0, lines
    before = {d: tree_hashes(env[d]) for d in "ab"}

    code, lines = run(capsys, "report", "duplicates")

    assert code == 0, lines
    r = result(lines)
    out = Path(r["report"])
    assert out.parent == tmp_path / "home" / "reports" and out.exists()
    assert r["clusters"] == 8 and r["files"] == 16
    assert r["reclaimable_bytes"] == sum(p.stat().st_size for p in env["a"].rglob("*") if p.is_file())
    by_label = {v["volume"]: v for v in r["volumes"]}
    assert by_label["OLDA"]["only_here"] == 0 and by_label["OLDB"]["only_here"] == 1
    assert not [e for e in lines if e["event"] == "warning"]
    assert "Photos/2024/A/DSC_0001.NEF" in out.read_text()
    assert {d: tree_hashes(env[d]) for d in "ab"} == before  # read-only on the drives

    out2 = tmp_path / "mine.md"
    code, lines = run(capsys, "report", "duplicates", "--volume", str(env["b"]), "--out", str(out2))
    assert code == 0 and result(lines)["clusters"] == 8 and out2.exists()


def test_cli_report_warns_about_unhashed_files(capsys, env, tmp_path: Path) -> None:
    assign_and_index(capsys, env["a"], "OLDA")  # indexed, never hashed

    code, lines = run(capsys, "report", "duplicates", "--out", str(tmp_path / "r.md"))

    assert code == 0
    assert result(lines)["clusters"] == 0
    assert [e["code"] for e in lines if e["event"] == "warning"] == ["unhashed_files"]


def test_cli_report_refuses_volumes_destination(capsys, env) -> None:
    code, lines = run(capsys, "report", "duplicates", "--out", "/Volumes/Nope/r.md")
    assert code == 1
    assert [e["code"] for e in lines if e["event"] == "error"] == ["ValueError"]
    assert not Path("/Volumes/Nope").exists()
