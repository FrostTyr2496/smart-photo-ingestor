"""Runs started outside the web UI (ui-11): progress files, `ps` parsing, catalog counts and stale jobs.

Everything uses tmp_path homes and catalogs, and injected process lists. Never /Volumes, never the real `ps` output.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path

import pytest

from photovault import activity, cli
from photovault.activity import ActivityEmitter, EngineProcess, guess_words, parse_etime, parse_ps, snapshot
from photovault.catalog import db, repo

SERVER = 999_001


def proc(pid: int, text: str, *, ppid: int = 1, elapsed: int = 120) -> EngineProcess:
    return EngineProcess(pid=pid, ppid=ppid, elapsed_s=elapsed, words=guess_words(text), text=text)


# --------------------------------------------------------------------------- ps parsing

@pytest.mark.parametrize("value, seconds", [("00:07", 7), ("12:34", 754), ("01:02:03", 3723), ("2-00:00:01", 172801)])
def test_parse_etime(value: str, seconds: int) -> None:
    assert parse_etime(value) == seconds


@pytest.mark.parametrize("text, words", [
    ("--json index /Volumes/Big Drive", ["index", "/Volumes/Big Drive"]),
    ("hash /Volumes/A B --max-mbps 50 --skip-prefix X", ["hash", "/Volumes/A B", "--max-mbps", "50", "--skip-prefix", "X"]),
    ("cards check /Volumes/NIKON Z 6_2", ["cards", "check", "/Volumes/NIKON Z 6_2"]),
    ("jobs", ["jobs"]),
])
def test_guess_words_rejoins_paths_with_spaces(text: str, words: list[str]) -> None:
    assert guess_words(text) == words


def test_parse_ps_keeps_engine_processes_only() -> None:
    out = "\n".join([
        "  101     1   01:00 uv run photovault --json index /Volumes/Big Drive",
        "  102   101   01:00 /opt/py/Python /repo/.venv/bin/photovault --json index /Volumes/Big Drive",
        "  103     1   05:00 /opt/py/Python /repo/.venv/bin/photovault serve",
        "  104   103   00:10 /opt/py/python -m photovault --json hash /Volumes/X",
        "  105     1   00:01 grep photovault",
        "  106     1   00:01 vim photovault/activity.py",
        "garbage",
    ])
    procs = parse_ps(out)
    assert [(p.pid, p.ppid, p.words) for p in procs] == [
        (102, 101, ["index", "/Volumes/Big Drive"]),
        (103, 1, ["serve"]),
        (104, 103, ["hash", "/Volumes/X"]),
    ]
    assert procs[0].elapsed_s == 60


# --------------------------------------------------------------------------- the progress file

def read_files(home: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(activity.activity_dir(home).glob("*.json"))]


def test_emitter_writes_progress_and_removes_it_at_close(tmp_path: Path) -> None:
    out = io.StringIO()
    em = ActivityEmitter(out, json_mode=True)
    em.track(tmp_path, ["--json", "index", "/Volumes/Big Drive"])
    em.emit("progress", phase="index", current=500, total=2000, message="")
    em.emit("warning", code="read_error", message="a.NEF: EIO")
    (state,) = read_files(tmp_path)
    assert state["pid"] == os.getpid()
    assert state["words"] == ["index", "/Volumes/Big Drive"]
    assert state["progress"] == {"phase": "index", "current": 500, "total": 2000, "message": ""}
    assert state["warnings"] == 1 and state["warning"] == "a.NEF: EIO"
    assert out.getvalue().count("\n") == 2  # the JSON-lines stream is unchanged
    em.close()
    assert read_files(tmp_path) == []


def test_emitter_throttles_but_writes_every_phase_change(tmp_path: Path) -> None:
    em = ActivityEmitter(io.StringIO())
    em.track(tmp_path, ["hash", "/Volumes/X"])
    em.emit("progress", phase="hash", current=0, total=10, message="")
    em.emit("progress", phase="hash", current=5, total=10, message="")  # within WRITE_EVERY_S: not written
    assert read_files(tmp_path)[0]["progress"]["current"] == 0
    em.emit("progress", phase="done", current=10, total=10, message="")
    assert read_files(tmp_path)[0]["progress"]["phase"] == "done"


def test_quick_commands_and_serve_leave_no_file(tmp_path: Path) -> None:
    em = ActivityEmitter(io.StringIO())
    em.track(tmp_path, ["jobs"])
    em.emit("result", jobs=[])
    em.emit("warning", code="x", message="y")
    assert not activity.activity_dir(tmp_path).exists()
    sv = ActivityEmitter(io.StringIO())
    sv.track(tmp_path, ["serve"])
    sv.emit("progress", phase="serve", message="at http://127.0.0.1")
    assert not activity.activity_dir(tmp_path).exists()


def test_emitter_ignores_an_unwritable_home(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a folder")
    em = ActivityEmitter(io.StringIO())
    em.track(blocker, ["index", "/x"])
    em.emit("progress", phase="index", current=1, total=2, message="")  # must not raise
    em.close()


def test_cli_run_cleans_up_its_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    home = tmp_path / "home"
    monkeypatch.setenv("PHOTOVAULT_HOME", str(home))
    assert cli.main(["--json", "catalog", "init"]) == 0
    assert cli.main(["--json", "jobs"]) == 0
    assert read_files(home) == []


# --------------------------------------------------------------------------- snapshot

@pytest.fixture
def conn(tmp_path: Path):
    c = db.connect(tmp_path / "catalog.sqlite")
    db.migrate(c)
    repo.upsert_volume(c, volume_id="vol-big", role="LEGACY", label="Big", fs_type="ntfs", da_volume_uuid="U1",
                       marker_id=None, capacity_bytes=12 * 10**12, mount_path="/Volumes/Big Drive")
    yield c
    c.close()


def running_job(conn, kind: str, mount: str, created_at: str = "2026-01-01T00:00:00Z") -> int:
    job_id = repo.create_job(conn, kind=kind, source_volume_id="vol-big", target_volume_id=None,
                             params={"mount": mount})
    repo.set_job_state(conn, job_id, "running")
    conn.execute("UPDATE jobs SET created_at = ? WHERE job_id = ?", (created_at, job_id))
    return job_id


def instance(conn, rel: str, *, state: str = "indexed", seen: str = "2026-01-01T00:05:00Z",
             verified_at: str | None = None) -> None:
    repo.upsert_instance(conn, volume_id="vol-big", rel_path=rel, size=1, mtime_ns=1, kind="photo", state="indexed")
    conn.execute("UPDATE instances SET state = ?, last_seen_at = ?, verified_at = ? WHERE rel_path = ?",
                 (state, seen, verified_at, rel))


def test_index_run_without_file_gets_catalog_counts(conn, tmp_path: Path) -> None:
    job = running_job(conn, "index_volume", "/Volumes/Big Drive")
    instance(conn, "a.NEF")
    instance(conn, "b.NEF", seen="2026-01-01T00:09:00Z")
    instance(conn, "old.NEF", seen="2025-06-01T00:00:00Z")  # seen by an earlier run: not counted
    snap = snapshot(conn, tmp_path, server_pid=SERVER, processes=[proc(200, "--json index /Volumes/Big Drive")])
    (entry,) = snap["activity"]
    assert entry["source"] == "catalog" and entry["progress"] is None
    assert entry["words"] == ["index", "/Volumes/Big Drive"]
    assert entry["job"]["job_id"] == job
    assert (entry["job"]["current"], entry["job"]["total"]) == (2, None)
    assert entry["job"]["last_write_at"] == "2026-01-01T00:09:00Z"
    assert snap["stale_jobs"] == []


def test_hash_run_counts_hashed_and_left(conn, tmp_path: Path) -> None:
    running_job(conn, "baseline_hash", "/Volumes/Big Drive")
    instance(conn, "a.NEF", state="verified", verified_at="2026-01-01T00:01:00Z")
    instance(conn, "b.NEF", state="verified", verified_at="2025-01-01T00:00:00Z")  # hashed before this job
    instance(conn, "c.NEF")
    instance(conn, "d.NEF")
    snap = snapshot(conn, tmp_path, server_pid=SERVER, processes=[proc(200, "hash /Volumes/Big Drive")])
    job = snap["activity"][0]["job"]
    assert (job["current"], job["total"], job["last_write_at"]) == (1, 3, "2026-01-01T00:01:00Z")


def test_progress_file_wins_and_dead_files_are_removed(conn, tmp_path: Path) -> None:
    folder = activity.activity_dir(tmp_path)
    folder.mkdir()
    (folder / "200.json").write_text(json.dumps({
        "words": ["index", "/Volumes/Big Drive"], "progress": {"phase": "index", "current": 5, "total": 9},
        "phase_started_at": "2026-01-01T00:00:00Z", "warning": None, "warnings": 0}))
    (folder / "777.json").write_text("{}")  # its process is gone
    running_job(conn, "index_volume", "/Volumes/Big Drive")
    snap = snapshot(conn, tmp_path, server_pid=SERVER, processes=[proc(200, "index /Volumes/Big Drive")])
    (entry,) = snap["activity"]
    assert entry["source"] == "progress_file"
    assert entry["progress"]["total"] == 9
    assert not (folder / "777.json").exists()


def test_jobs_without_a_process_are_stale(conn, tmp_path: Path) -> None:
    older = running_job(conn, "index_volume", "/Volumes/Big Drive")
    newer = running_job(conn, "index_volume", "/Volumes/Big Drive")  # one live process claims only one job
    other = running_job(conn, "baseline_hash", "/Volumes/PP 1")
    snap = snapshot(conn, tmp_path, server_pid=SERVER, processes=[proc(200, "index /Volumes/Big Drive")])
    assert snap["activity"][0]["job"]["job_id"] == newer  # the newest running job is the live one
    assert snap["stale_jobs"] == []  # idx-9: ownerless jobs are paused, so the list empties by itself
    states = {r["job_id"]: r["state"] for r in conn.execute("SELECT job_id, state FROM jobs")}
    assert states == {older: "paused", newer: "running", other: "paused"}


def test_pause_ownerless_names_the_job_and_spares_live_ones(conn, tmp_path: Path, monkeypatch) -> None:
    dead = running_job(conn, "index_volume", "/Volumes/PP 1")
    live = running_job(conn, "index_volume", "/Volumes/Big Drive")
    resumed = running_job(conn, "baseline_hash", "/Volumes/Other")
    out = io.StringIO()
    em = ActivityEmitter(out, json_mode=True)
    procs = [proc(200, "index /Volumes/Big Drive"), proc(201, f"resume {resumed}")]
    assert activity.pause_ownerless(conn, tmp_path, processes=procs, emit=em) == [dead]
    events = [json.loads(line) for line in out.getvalue().splitlines()]
    assert [e["event"] for e in events] == ["warning"] and f"job {dead}" in events[0]["message"]
    states = {r["job_id"]: r["state"] for r in conn.execute("SELECT job_id, state FROM jobs")}
    assert states == {dead: "paused", live: "running", resumed: "running"}
    monkeypatch.setattr(activity, "list_processes", lambda: None)  # ps unavailable: touch nothing
    assert activity.pause_ownerless(conn, tmp_path) == []


def test_server_and_its_children_are_hidden_but_keep_their_job_live(conn, tmp_path: Path) -> None:
    running_job(conn, "index_volume", "/Volumes/Big Drive")
    procs = [proc(SERVER, "serve"), proc(301, "serve --port 8799"),  # another UI server is not a run either
             proc(300, "--json index /Volumes/Big Drive", ppid=SERVER)]
    snap = snapshot(conn, tmp_path, server_pid=SERVER, processes=procs)
    assert snap["activity"] == [] and snap["stale_jobs"] == []


def test_unknown_process_list_reports_no_stale_jobs(conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    running_job(conn, "index_volume", "/Volumes/Big Drive")
    monkeypatch.setattr(activity, "list_processes", lambda: None)
    snap = snapshot(conn, tmp_path, server_pid=SERVER)
    assert snap == {**snap, "activity": [], "stale_jobs": [], "processes_known": False}
