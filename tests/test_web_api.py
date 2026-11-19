"""Local web API (ui-4): host guard, whitelist, drives, tasks, meta round-trip, suggestions, thumbnails.

Everything runs on tmp_path roots and a tmp catalog through a real server thread on port 0. Never /Volumes.
"""

from __future__ import annotations

import json
import re
import select
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from photovault import activity, cli, metadata, preview, volumes
from photovault.catalog import db, repo
from photovault.config import load_config
from photovault.geo import utc_text
from photovault.web import server as web
from tests.test_cli import fake_probe

# A stand-in engine: prints events, and with "/sleep-here" among its args installs a SIGINT handler that exits 4
# (the engine's "paused" code) and then waits, so pause can be tested without a real job.
CHILD = r'''
import json, signal, sys, time
def emit(**kw):
    print(json.dumps({"v": 1, **kw}), flush=True)
args = sys.argv[1:]
if "/sleep-here" in args:
    signal.signal(signal.SIGINT, lambda s, f: sys.exit(4))
    emit(event="progress", phase="ready", current=0, total=1, message="waiting")
    time.sleep(30)
emit(event="progress", phase="work", current=1, total=2, message="half")
print("plain text line", flush=True)
emit(event="result", args=args)
'''


def seed(conn, root: Path) -> dict[str, int]:
    """Two drives (one WORKING and mounted, one LEGACY and unplugged) and three groups."""
    repo.upsert_volume(conn, volume_id="vol-work", role="WORKING", label="WORK", fs_type="exfat",
                       da_volume_uuid="UUID-WORK", marker_id="m-work", capacity_bytes=10**12,
                       mount_path=root / "WORK")
    repo.upsert_volume(conn, volume_id="vol-leg", role="LEGACY", label="OLD", fs_type="apfs",
                       da_volume_uuid="UUID-LEG", marker_id=None, capacity_bytes=10**12,
                       mount_path=root / "LEG")
    ids = {}
    for key, shutter, stem in (("a", 1, "DSC_0001"), ("b", 2, "DSC_0002"), ("c", 3, "OLD_0003")):
        ids[key] = repo.insert_group(conn, body_id=None, shutter_count=shutter,
                                     capture_time="2026-11-10T21:00:00", capture_offset=None,
                                     time_source="exif", canonical_stem=f"M{shutter:04d}_{stem}",
                                     original_stem=stem, original_num=shutter, shoot_id=None)
    a_asset = repo.insert_asset(conn, sha256="a" * 64, size=10, partial_sha256="aa", kind="photo",
                                group_id=ids["a"], group_role="primary")
    repo.upsert_instance(conn, volume_id="vol-work", rel_path="Photos/DSC_0001.JPG", size=10, mtime_ns=1,
                         kind="photo", state="indexed", asset_id=a_asset)
    c_asset = repo.insert_asset(conn, sha256="c" * 64, size=10, partial_sha256="cc", kind="photo",
                                group_id=ids["c"], group_role="primary")
    repo.upsert_instance(conn, volume_id="vol-leg", rel_path="Old/OLD_0003.JPG", size=10, mtime_ns=1,
                         kind="photo", state="indexed", asset_id=c_asset)
    repo.upsert_geo_point(conn, utc_time=utc_text(datetime(2026, 11, 10, 21, 5, tzinfo=UTC)), lat=51.6, lon=-0.2,
                          alt=None, source="phone", source_ref="p1")
    return ids


class Client:
    def __init__(self, port: int) -> None:
        self.port = port

    def call(self, method: str, path: str, body=None, *, host: str | None = None, raw: bytes | None = None,
             content_type: str | None = "application/json", origin: str | None = None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        if content_type is not None:
            req.add_header("Content-Type", content_type)
        if host is not None:
            req.add_header("Host", host)
        if origin is not None:
            req.add_header("Origin", origin)
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                status, payload, ctype = r.status, r.read(), r.headers.get("Content-Type", "")
        except urllib.error.HTTPError as exc:
            status, payload, ctype = exc.code, exc.read(), exc.headers.get("Content-Type", "")
        if ctype.startswith("application/json"):
            return status, json.loads(payload)
        return status, payload

    def wait_task(self, task_id: int, done=lambda d: d["state"] != "running", timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        while True:
            _, detail = self.call("GET", f"/api/tasks/{task_id}")
            if done(detail) or time.time() > deadline:
                return detail
            time.sleep(0.05)


@pytest.fixture
def srv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    home = tmp_path / "home"
    monkeypatch.setenv("PHOTOVAULT_HOME", str(home))
    monkeypatch.setattr(volumes, "probe", fake_probe)
    root = tmp_path / "Volumes"
    (root / "WORK").mkdir(parents=True)
    volumes.write_marker(root / "WORK", "m-work")
    (root / "CARD1" / "DCIM").mkdir(parents=True)

    catalog = load_config().catalog_path
    conn = db.connect(catalog)
    db.migrate(conn)
    ids = seed(conn, root)
    conn.close()

    app = web.WebApp(catalog_path=catalog, home=home, volumes_root=root,
                     runner=web.TaskRunner([sys.executable, "-c", CHILD]))
    httpd = web.make_server(app, port=0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    client = Client(port)
    client.root, client.ids, client.home, client.app = root, ids, home, app
    yield client
    httpd.shutdown()
    httpd.server_close()


def host_of(client: Client, name: str = "127.0.0.1") -> str:
    return f"{name}:{client.port}"


# --------------------------------------------------------------------------- host guard and static files

def test_host_guard_rejects_other_hosts(srv: Client) -> None:
    assert srv.call("GET", "/api/settings", host="evil.example:%d" % srv.port)[0] == 403
    assert srv.call("GET", "/api/settings", host="127.0.0.1:1")[0] == 403
    assert srv.call("GET", "/api/settings", host="127.0.0.1")[0] == 403
    assert srv.call("GET", "/", host="evil.example:%d" % srv.port)[0] == 403
    assert srv.call("GET", "/api/settings", host=host_of(srv))[0] == 200
    assert srv.call("GET", "/api/settings", host=host_of(srv, "localhost"))[0] == 200


def test_static_files_stay_inside_static_dir(srv: Client) -> None:
    status, body = srv.call("GET", "/")
    assert status == 200 and b"<html" in body.lower()
    assert srv.call("GET", "/static/../../../photovault/cli.py")[0] == 404
    assert srv.call("GET", "/static/%2e%2e/%2e%2e/cli.py")[0] == 404
    assert srv.call("GET", "/static/does-not-exist.js")[0] == 404
    assert srv.call("GET", "/api/nope")[0] == 404


def test_next_ui_is_served_with_its_assets(srv: Client) -> None:
    """The UI (D-059) is a committed Vite build: / serves it (and /next, its address before rv-6), and every file it
    loads is there. The classic plain-JS UI is gone."""
    assert srv.call("GET", "/static/app.js")[0] == 404
    for path in ("/", "/next", "/next/"):
        status, body = srv.call("GET", path)
        assert status == 200 and b'<div id="root">' in body
    refs = re.findall(rb'(?:src|href)="(/static/next/[^"]+)"', body)
    assert any(r.endswith(b".js") for r in refs) and any(r.endswith(b".css") for r in refs)
    for ref in refs:
        assert srv.call("GET", ref.decode())[0] == 200, ref


# --------------------------------------------------------------------------- POST content type and origin

def test_post_requires_json_content_type(srv: Client) -> None:
    for ctype in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
        status, _ = srv.call("POST", "/api/settings", {"creator": "X"}, content_type=ctype)
        assert status == 403
    status, _ = srv.call("POST", "/api/settings", raw=b'{"creator":"X"}', content_type=None)
    assert status == 403
    assert srv.call("GET", "/api/settings")[1]["settings"]["creator"] == ""
    status, _ = srv.call("POST", "/api/settings", {"creator": "Kept"})
    assert status == 200


def test_post_rejects_foreign_origin(srv: Client) -> None:
    for origin in ("http://evil.example", f"https://127.0.0.1:{srv.port}", f"http://127.0.0.1:1",
                   "null", f"http://127.0.0.1:{srv.port}.evil.example"):
        assert srv.call("POST", "/api/settings", {"creator": "X"}, origin=origin)[0] == 403
    assert srv.call("GET", "/api/settings")[1]["settings"]["creator"] == ""


def test_post_accepts_local_origin_or_none(srv: Client) -> None:
    for origin in (None, f"http://127.0.0.1:{srv.port}", f"http://localhost:{srv.port}"):
        status, _ = srv.call("POST", "/api/settings", {"creator": "Local"}, origin=origin)
        assert status == 200
    status, _ = srv.call("POST", "/api/settings", {"creator": "Charset"},
                         content_type="application/json; charset=utf-8")
    assert status == 200


# --------------------------------------------------------------------------- drives, jobs, shoots

def test_drives_lists_mounted_then_remembered(srv: Client) -> None:
    status, body = srv.call("GET", "/api/drives")
    assert status == 200
    drives = body["drives"]
    by_label = {d["label"]: d for d in drives}
    work = by_label["WORK"]
    assert work["mounted"] is True and work["role"] == "WORKING" and work["volume_id"] == "vol-work"
    assert work["mount"] == str(srv.root / "WORK") and work["fs"] == "exfat"
    old = by_label["OLD"]
    assert old["mounted"] is False and old["mount"] is None and old["role"] == "LEGACY"
    assert old["last_seen_at"]
    assert set(work) == {"mount", "role", "label", "fs", "size", "free", "volume_id", "mounted",
                         "looks_like_card", "last_seen_at", "meta_scanned", "steps", "local", "key", "ignored"}
    assert work["meta_scanned"] is False  # the seeded photo has no group_meta row yet
    assert set(work["steps"]) == {"index", "hash", "scan", "sidecars"}  # last-run time per workflow step
    # mounted first
    assert [d["mounted"] for d in drives] == sorted([d["mounted"] for d in drives], reverse=True)


def test_drives_shows_unregistered_card(srv: Client) -> None:
    drives = {d["mount"]: d for d in srv.call("GET", "/api/drives")[1]["drives"] if d["mount"]}
    card = drives[str(srv.root / "CARD1")]
    assert card["role"] == "CARD (new)" and card["looks_like_card"] is True and card["volume_id"] is None


def test_ignore_drive_round_trip(srv: Client) -> None:
    drives = srv.call("GET", "/api/drives")[1]["drives"]
    keys = {d["label"] or d["mount"]: d["key"] for d in drives}
    assert keys["WORK"] == "vol-work" and keys[str(srv.root / "CARD1")] == "mount:CARD1"
    assert not any(d["ignored"] for d in drives)

    for key in ("mount:CARD1", "vol-leg"):
        status, body = srv.call("POST", "/api/drives/ignore", {"key": key, "ignored": True})
        assert status == 200
    assert body["ignored"] == ["mount:CARD1", "vol-leg"]
    ignored = {d["key"] for d in srv.call("GET", "/api/drives")[1]["drives"] if d["ignored"]}
    assert ignored == {"mount:CARD1", "vol-leg"}  # still listed: the sidebar folds them away

    assert srv.call("POST", "/api/drives/ignore", {"key": "vol-leg", "ignored": False})[1]["ignored"] == ["mount:CARD1"]
    assert srv.call("POST", "/api/drives/ignore", {"key": "", "ignored": True})[0] == 400
    assert srv.call("POST", "/api/drives/ignore", {"key": "x", "ignored": "yes"})[0] == 400
    assert srv.call("GET", "/api/drives/ignore")[0] == 405


def open_events(client: Client, host: str | None = None):
    """A raw SSE connection. The socket timeout is 3 s, so a stream that stalls fails the test."""
    req = urllib.request.Request(f"http://127.0.0.1:{client.port}/api/events",
                                 headers={"Host": host or host_of(client)})
    return urllib.request.urlopen(req, timeout=3)


def read_event(resp, name: bytes = b"event: drives") -> bytes:
    """Read SSE lines until one starts with `name`; the 3 s socket timeout bounds each read."""
    deadline = time.time() + 3.0
    while time.time() < deadline:
        line = resp.readline()
        if not line:
            break
        if line.startswith(name):
            return line
    pytest.fail(f"no {name!r} line within 3 s")


def wait_for(predicate, timeout: float = 3.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_new_folder_bumps_generation_and_sse_reports_it(srv: Client) -> None:
    resp = open_events(srv)
    try:
        assert resp.headers["Content-Type"].startswith("text/event-stream")
        assert resp.headers["Cache-Control"] == "no-cache"
        read_event(resp)  # the initial event
        before = srv.app.watcher.generation
        (srv.root / "NEWDRIVE").mkdir()
        assert wait_for(lambda: srv.app.watcher.generation > before)
        read_event(resp)  # the change event, within the socket timeout
    finally:
        resp.close()


def test_sse_does_not_block_other_requests(srv: Client) -> None:
    resp = open_events(srv)
    try:
        read_event(resp)
        assert srv.call("GET", "/api/settings")[0] == 200
        assert srv.call("GET", "/api/drives")[0] == 200
    finally:
        resp.close()


def test_events_host_guard(srv: Client) -> None:
    status, body = srv.call("GET", "/api/events", host=f"evil.example:{srv.port}")
    assert status == 403 and body == {"error": "host not allowed"}


def test_kqueue_watch_reports_changes_without_polling(tmp_path: Path) -> None:
    if not hasattr(select, "kqueue"):
        pytest.skip("kqueue is not available on this platform")
    root = tmp_path / "vols"
    root.mkdir()
    watcher = web.VolumeWatcher(root, poll_s=60)  # only kqueue can answer within the wait below
    watcher.start()
    try:
        (root / "NEW").mkdir()
        assert watcher.wait_change(0, timeout=3.0)
    finally:
        watcher.stop()
    assert watcher.stopped


def test_stat_fallback_reports_changes(tmp_path: Path) -> None:
    root = tmp_path / "vols"
    root.mkdir()
    watcher = web.VolumeWatcher(root, use_kqueue=False, poll_s=0.05)
    watcher.start()
    try:
        (root / "NEW").mkdir()
        assert watcher.wait_change(0, timeout=3.0)
    finally:
        watcher.stop()


def test_jobs_and_shoots_are_lists(srv: Client) -> None:
    assert srv.call("GET", "/api/jobs") == (200, {"jobs": []})
    assert srv.call("GET", "/api/shoots") == (200, {"shoots": []})


def test_cli_drives_output_unchanged(srv: Client, capsys) -> None:
    code = cli.main(["--json", "drives", "--root", str(srv.root)])
    assert code == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    result = [e for e in lines if e["event"] == "result"][-1]
    assert set(result["drives"][0]) == {"mount", "role", "label", "fs", "size", "free"}
    roles = {Path(r["mount"]).name: r["role"] for r in result["drives"]}
    assert roles == {"CARD1": "CARD (new)", "WORK": "WORKING"}


# --------------------------------------------------------------------------- whitelist and argv

def test_run_rejects_unknown_and_bad_arguments(srv: Client) -> None:
    mount = str(srv.root / "WORK")
    bad = [
        {"cmd": "rm"},                                                    # not whitelisted
        {"cmd": "card_status", "args": {}},                               # missing argument
        {"cmd": "card_status", "args": {"mount": "-x"}},                  # flag injection
        {"cmd": "card_status", "args": {"mount": "relative/path"}},       # not absolute
        {"cmd": "card_status", "args": {"mount": mount, "extra": 1}},     # unknown argument
        {"cmd": "volumes_assign", "args": {"mount": mount, "role": "WORKING"}},
        {"cmd": "assign", "args": {"mount": mount, "role": "CARD"}},      # role not allowed
        {"cmd": "assign", "args": {"mount": mount, "role": "WORKING", "label": "--force"}},
        {"cmd": "resume", "args": {"job_id": "1"}},                       # must be an integer
        {"cmd": "resume", "args": {"job_id": -1}},
        {"cmd": "ingest", "args": {"source": mount, "target": mount, "dry_run": "yes"}},
        {"cmd": "ingest", "args": {"source": mount, "target": mount, "limit": 0}},
        {"cmd": "ingest", "args": {"source": mount, "target": mount, "limit": "10"}},
        {"cmd": "meta_refs", "args": {"folder": mount, "label": "a\nb"}},
    ]
    for body in bad:
        status, payload = srv.call("POST", "/api/run", body)
        assert status == 400, (body, payload)
    assert srv.call("POST", "/api/run", raw=b"{not json")[0] == 400
    assert srv.call("POST", "/api/run", ["list"])[0] == 400


@pytest.mark.parametrize("cmd, args, expected", [
    ("assign", {"mount": "/m", "role": "WORKING", "label": "WORK"},
     ["volumes", "assign", "/m", "--role", "WORKING", "--label", "WORK"]),
    ("assign", {"mount": "/m", "role": "WORKING", "change_role": True},
     ["volumes", "assign", "/m", "--role", "WORKING", "--change-role"]),
    ("ingest", {"source": "/c", "target": "/t", "name": "Night", "dry_run": True},
     ["ingest", "--source", "/c", "--target", "/t", "--name", "Night", "--dry-run"]),
    ("ingest", {"source": "/c", "target": "/t", "limit": 10, "dry_run": True},
     ["ingest", "--source", "/c", "--target", "/t", "--limit", "10", "--dry-run"]),
    ("backup", {"card": "vid", "source": "/w", "target": "/b", "follow": 7},
     ["backup", "--to", "/b", "--card", "vid", "--from", "/w", "--follow", "7"]),
    ("backup", {"card": "vid", "source": "/w", "target": "/b", "dry_run": True},
     ["backup", "--to", "/b", "--card", "vid", "--from", "/w", "--dry-run"]),
    ("backup", {"from_folder": "/old/Eclipse", "target": "/b", "dry_run": True},
     ["backup", "--to", "/b", "--from-folder", "/old/Eclipse", "--dry-run"]),
    ("backup", {"card": "vid", "make_green": True, "dry_run": True},
     ["backup", "--card", "vid", "--make-green", "--dry-run"]),
    ("backup", {"card": "vid", "make_green": True}, ["backup", "--card", "vid", "--make-green"]),
    ("resume", {"job_id": 3}, ["resume", "3"]),
    ("card_status", {"mount": "/c"}, ["card-status", "/c"]),
    ("index", {"mount": "/w"}, ["index", "/w"]),
    ("hash", {"mount": "/w"}, ["hash", "/w"]),
    ("meta_scan", {"mount": "/w"}, ["meta", "scan", "--volume", "/w"]),
    ("meta_scan", {"mount": "/w", "rescan": True}, ["meta", "scan", "--volume", "/w", "--rescan"]),
    ("meta_refs", {"folder": "/f", "label": "phone"}, ["meta", "refs", "--folder", "/f", "--label", "phone"]),
    ("meta_sidecars", {"mount": "/w", "dry_run": True}, ["meta", "sidecars", "--volume", "/w", "--dry-run"]),
    ("card_check", {"mount": "/c"}, ["cards", "check", "/c"]),
])
def test_build_argv_maps_each_command_to_parsable_cli(cmd, args, expected) -> None:
    argv = web.build_argv(cmd, args)
    assert argv == expected
    cli.build_parser().parse_args(["--json", *argv])  # the real parser accepts it


# --------------------------------------------------------------------------- tasks

def test_task_runs_and_reports_events(srv: Client) -> None:
    mount = str(srv.root / "CARD1")
    status, body = srv.call("POST", "/api/run", {"cmd": "card_status", "args": {"mount": mount}})
    assert status == 200 and isinstance(body["task_id"], int)
    detail = srv.wait_task(body["task_id"])
    assert detail["state"] == "done" and detail["exit_code"] == 0
    kinds = [e["event"] for e in detail["events"]]
    assert kinds == ["progress", "text", "result"]
    assert detail["events"][1]["line"] == "plain text line"
    assert detail["events"][2]["args"] == ["card-status", mount]
    assert detail["latest_progress"]["phase"] == "work"
    # `since` returns only events after that index
    since = detail["events"][0]["seq"]
    later = srv.call("GET", f"/api/tasks/{body['task_id']}?since={since}")[1]
    assert [e["seq"] for e in later["events"]] == [1, 2]
    assert srv.call("GET", "/api/tasks")[1]["tasks"][0]["task_id"] == body["task_id"]


def test_pause_sends_sigint_and_marks_paused(srv: Client) -> None:
    status, body = srv.call("POST", "/api/run", {"cmd": "card_status", "args": {"mount": "/sleep-here"}})
    task_id = body["task_id"]
    srv.wait_task(task_id, done=lambda d: any(e.get("phase") == "ready" for e in d["events"]))
    assert srv.call("POST", f"/api/tasks/{task_id}/pause")[0] == 200
    detail = srv.wait_task(task_id)
    assert detail["state"] == "paused" and detail["exit_code"] == 4
    assert srv.call("POST", f"/api/tasks/{task_id}/pause")[0] == 409   # not running any more
    assert srv.call("POST", "/api/tasks/9999/pause")[0] == 404
    assert srv.call("GET", "/api/tasks/9999")[0] == 404


def test_spawn_error_marks_task_failed() -> None:
    runner = web.TaskRunner(["/no/such/python-binary-for-test"])
    task_id = runner.start(["card-status", "/x"])
    assert runner.get(task_id).state == "failed"
    assert runner.get(task_id).events[0]["event"] == "error"


def test_parse_line_keeps_non_json_as_text() -> None:
    assert web.parse_line('{"v":1,"event":"result","x":1}\n')["event"] == "result"
    assert web.parse_line("Traceback (most recent call last):\n") == {
        "event": "text", "line": "Traceback (most recent call last):"}
    assert web.parse_line("[1, 2]") == {"event": "text", "line": "[1, 2]"}


# --------------------------------------------------------------------------- groups and meta

def test_photo_days_and_day_filter(srv: Client) -> None:
    status, body = srv.call("GET", "/api/photo-days")
    assert status == 200 and body["days"] and body["missing_counts"]["groups"] == sum(d["groups"] for d in body["days"])
    day = body["days"][0]
    groups = srv.call("GET", f"/api/groups?day={day['day']}")[1]["groups"]
    assert len(groups) == day["groups"]

def test_groups_carry_utc_time_body_and_sequence(srv: Client) -> None:
    a = srv.ids["a"]
    rows = {g["group_id"]: g for g in srv.call("GET", "/api/groups")[1]["groups"]}
    assert rows[a]["utc_time"] == "2026-11-10T21:00:00+00:00"  # default offset +00:00
    assert {"body_id", "sequence_id"} <= set(rows[a])
    assert srv.call("POST", "/api/settings", {"default_offset": "+01:00"})[0] == 200
    srv.call("POST", "/api/groups/meta", {"group_ids": [a], "fields": {"time_shift_s": 60}})
    rows = {g["group_id"]: g for g in srv.call("GET", "/api/groups")[1]["groups"]}
    assert rows[a]["utc_time"] == "2026-11-10T20:01:00+00:00"  # 21:00 at +01:00, shifted a minute later
    assert rows[srv.ids["b"]]["utc_time"] == "2026-11-10T20:00:00+00:00"


def test_groups_meta_round_trip(srv: Client) -> None:
    a, b = srv.ids["a"], srv.ids["b"]
    status, body = srv.call("GET", "/api/groups")
    assert status == 200
    assert {"groups", "missing_counts"} <= set(body)
    assert body["missing_counts"]["groups"] == 3 and body["missing_counts"]["gps"] == 3
    row = next(g for g in body["groups"] if g["group_id"] == a)
    assert row["keywords"] == [] and row["dirty"] in (None, False)
    assert row["rel_path"] == "Photos/DSC_0001.JPG" and row["volume_id"] == "vol-work"

    fields = {"creator": "Mush", "keywords_add": ["beach", "sun"], "lat": 51.5, "lon": -0.12,
              "gps_source": "map", "place_name": "Brighton", "city": "Brighton", "state": "Sussex",
              "country": "Freedonia"}
    status, body = srv.call("POST", "/api/groups/meta", {"group_ids": [a], "fields": fields})
    assert status == 200 and body == {"updated": 1}

    body = srv.call("GET", "/api/groups?missing=gps")[1]
    assert [g["group_id"] for g in body["groups"]] == [b, srv.ids["c"]]
    body = srv.call("GET", "/api/groups?missing=creator")[1]
    assert [g["group_id"] for g in body["groups"]] == [b, srv.ids["c"]]
    body = srv.call("GET", "/api/groups?volume_id=vol-work&missing=creator")[1]
    assert body["groups"] == []  # a (the only group on vol-work) now has a creator
    row = next(g for g in srv.call("GET", "/api/groups")[1]["groups"] if g["group_id"] == a)
    assert row["keywords"] == ["beach", "sun"] and row["creator"] == "Mush" and row["dirty"] is True
    assert row["lat"] == 51.5 and row["gps_source"] == "map"
    assert (row["city"], row["state"], row["country"]) == ("Brighton", "Sussex", "Freedonia")

    # keyword removal (case-insensitive) through the same endpoint
    srv.call("POST", "/api/groups/meta", {"group_ids": [a], "fields": {"keywords_remove": ["BEACH"]}})
    row = next(g for g in srv.call("GET", "/api/groups")[1]["groups"] if g["group_id"] == a)
    assert row["keywords"] == ["sun"]


def test_groups_meta_validation(srv: Client) -> None:
    a = srv.ids["a"]
    post = lambda body: srv.call("POST", "/api/groups/meta", body)[0]  # noqa: E731
    assert post({"group_ids": [a], "fields": {"bogus": 1}}) == 400      # unknown field (ValueError)
    assert post({"group_ids": [999], "fields": {"creator": "x"}}) == 400  # unknown group
    assert post({"group_ids": [a], "fields": {"city": 5}}) == 400         # place fields are strings or null
    assert post({"group_ids": [], "fields": {"creator": "x"}}) == 400
    assert post({"group_ids": [a], "fields": {}}) == 400
    assert post({"group_ids": [a], "fields": {"gps_source": "guess"}}) == 400
    assert post({"group_ids": [a], "fields": {"lat": "51"}}) == 400
    assert srv.call("GET", "/api/groups?missing=bogus")[0] == 400
    assert srv.call("GET", "/api/groups?day=bogus")[0] == 400
    assert srv.call("GET", "/api/groups?after=yesterday")[0] == 400
    assert srv.call("GET", "/api/photo-days?missing=bogus")[0] == 400
    assert srv.call("GET", "/api/groups?limit=abc")[0] == 400


def test_suggestions_and_unknown_group(srv: Client) -> None:
    status, body = srv.call("GET", f"/api/groups/{srv.ids['a']}/suggestions")
    assert status == 200
    first = body["suggestions"][0]
    assert first["source"] == "phone" and first["delta_s"] == 300 and first["lat"] == pytest.approx(51.6)
    assert set(first) >= {"lat", "lon", "alt", "source", "source_ref", "delta_s", "confidence"}
    assert srv.call("GET", "/api/groups/999/suggestions")[0] == 404


def test_places_endpoint(srv: Client) -> None:
    status, body = srv.call("GET", f"/api/groups/{srv.ids['a']}/places?window_s=3600")
    assert status == 200 and body == {"places": []}  # the fixture's phone point has no place name
    assert srv.call("GET", "/api/groups/999/places")[0] == 404


# --------------------------------------------------------------------------- settings

def test_settings_defaults_and_updates(srv: Client) -> None:
    status, body = srv.call("GET", "/api/settings")
    assert body["settings"] == {"creator": "", "copyright": "", "default_offset": "+00:00", "match_window_s": 1800,
                                "match_reach_s": 43200,
                                "mac_folder": "~/Sandbox"}
    status, body = srv.call("POST", "/api/settings", {"creator": " Mush ", "match_window_s": 600,
                                                      "default_offset": "+01:00"})
    assert status == 200
    assert body["settings"]["creator"] == "Mush" and body["settings"]["match_window_s"] == 600
    assert body["settings"]["default_offset"] == "+01:00"
    assert srv.call("POST", "/api/settings", {"unknown_key": 1})[0] == 400
    assert srv.call("POST", "/api/settings", {"match_window_s": 99999})[0] == 400
    status, body = srv.call("POST", "/api/settings", {"match_reach_s": "7200"})
    assert status == 200 and body["settings"]["match_reach_s"] == 7200
    assert srv.call("POST", "/api/settings", {"match_reach_s": 86401})[0] == 400
    assert srv.call("POST", "/api/settings", {"match_reach_s": "soon"})[0] == 400
    assert srv.call("POST", "/api/settings", {"default_offset": "nowhere"})[0] == 400
    assert srv.call("POST", "/api/settings", {})[0] == 400


# --------------------------------------------------------------------------- thumbnails

def test_thumb_from_mounted_drive_is_cached(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = srv.root / "WORK" / "Photos" / "DSC_0001.JPG"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"\xff\xd8\xff\xe0 not really a jpeg")
    seen = []

    def fake_preview(path: Path) -> bytes:
        seen.append(Path(path))
        return b"\xff\xd8\xff\xd9thumb"

    monkeypatch.setattr(preview, "extract_preview", fake_preview)
    gid = srv.ids["a"]
    status, data = srv.call("GET", f"/api/groups/{gid}/thumb")
    assert status == 200 and data == b"\xff\xd8\xff\xd9thumb"
    assert seen == [photo.resolve()]
    assert (srv.home / "thumbs" / f"{gid}.jpg").read_bytes() == data
    # second request is served from the cache, not re-extracted
    assert srv.call("GET", f"/api/groups/{gid}/thumb")[1] == data
    assert len(seen) == 1
    assert not list((srv.root / "WORK").rglob("*thumb*"))  # nothing written under the mount


def test_thumb_404_when_unmounted_or_no_file(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(preview, "extract_preview", lambda path: pytest.fail("must not extract"))
    assert srv.call("GET", f"/api/groups/{srv.ids['c']}/thumb")[0] == 404   # LEGACY drive is not mounted
    assert srv.call("GET", f"/api/groups/{srv.ids['b']}/thumb")[0] == 404   # no instance at all


def test_thumb_and_location_of_an_unhashed_file(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file indexed but not hashed has no asset link; its index job's record still finds it (rv-7)."""
    photo = srv.root / "WORK" / "Old" / "DSC_0009.NEF"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    conn = db.connect(srv.app.catalog_path)
    with repo.transaction(conn):
        gid = repo.insert_group(conn, body_id=None, shutter_count=None, capture_time="2026-07-01T08:00:00",
                                capture_offset="+00:00", time_source="exif", canonical_stem="DSC_0009",
                                original_stem="DSC_0009", original_num=None, shoot_id=None)
        repo.upsert_instance(conn, volume_id="vol-work", rel_path="Old/DSC_0009.NEF", size=1, mtime_ns=1, kind="raw",
                             state="indexed")
        job = repo.create_job(conn, kind="index_volume", source_volume_id="vol-work", target_volume_id=None, params={})
        repo.add_job_items(conn, job, [{"seq": 1, "src_rel": "Old/DSC_0009.NEF", "dst_rel": None, "group_id": gid,
                                        "role": "primary"}])
    conn.close()
    monkeypatch.setattr(preview, "extract_preview", lambda path: b"\xff\xd8\xff\xd9" if Path(path) == photo.resolve() else None)
    assert srv.call("GET", f"/api/groups/{gid}/thumb") == (200, b"\xff\xd8\xff\xd9")
    row = srv.call("GET", "/api/groups?day=2026-07-01&volume_id=vol-work")[1]["groups"][0]
    assert (row["group_id"], row["volume_id"], row["rel_path"]) == (gid, "vol-work", "Old/DSC_0009.NEF")


def test_thumb_404_when_no_preview(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = srv.root / "WORK" / "Photos" / "DSC_0001.JPG"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    monkeypatch.setattr(preview, "extract_preview", lambda path: None)
    assert srv.call("GET", f"/api/groups/{srv.ids['a']}/thumb")[0] == 404


# --------------------------------------------------------------------------- drive summary and Finder (ui-7)


def test_volume_summary_and_file_list(srv: Client) -> None:
    status, body = srv.call("GET", "/api/volumes/vol-work")
    assert status == 200
    assert body["drive"]["label"] == "WORK" and body["drive"]["mounted"] is True
    assert body["states"]["indexed"] == {"files": 1, "bytes": 10, "hashed": 1}
    assert body["states"]["verified"] == {"files": 0, "bytes": 0, "hashed": 0}
    assert body["files"] == 1 and body["bytes"] == 10
    assert body["kinds"] == [{"kind": "photo", "files": 1, "bytes": 10}]
    assert body["runs"] == []

    status, body = srv.call("GET", "/api/volumes/vol-work/files?state=indexed")
    assert status == 200
    assert [f["rel_path"] for f in body["files"]] == ["Photos/DSC_0001.JPG"]
    assert body["files"][0]["hashed"] is True and "verified_sha256" not in body["files"][0]
    assert srv.call("GET", "/api/volumes/vol-work/files?state=verified")[1]["files"] == []
    assert srv.call("GET", "/api/volumes/vol-work/files?state=bogus")[0] == 400
    assert srv.call("GET", "/api/volumes/nope")[0] == 404
    assert srv.call("POST", "/api/volumes/vol-work")[0] == 405


def test_volume_summary_usage_splits_photography_from_the_rest(srv: Client) -> None:
    """idx-13: usage is None until an index counts the drive, then photo vs non-photo with free space."""
    assert srv.call("GET", "/api/volumes/vol-work")[1]["usage"] is None
    c = db.connect(srv.app.catalog_path)
    try:
        cap = c.execute("SELECT capacity_bytes FROM volumes WHERE volume_id = 'vol-work'").fetchone()[0]
        with repo.transaction(c):
            repo.replace_volume_usage(c, "vol-work", {"lightroom": (2, 5), "audio": (3, 100), "disk_used": (0, 200)})
    finally:
        c.close()
    u = srv.call("GET", "/api/volumes/vol-work")[1]["usage"]
    assert u["photo"]["bytes"] == 10 + 5 and u["photo"]["files"] == 1 + 2
    assert u["non_photo"]["parts"] == [{"category": "audio", "files": 3, "bytes": 100}]
    assert u["unaccounted"] == 200 - 15 - 100 and u["used"] == 200
    assert u["free"] == (max(cap - 200, 0) if cap else None)


def test_volume_summary_lists_runs_with_their_results(srv: Client) -> None:
    c = db.connect(srv.app.catalog_path)
    with repo.transaction(c):
        job = repo.create_job(c, kind="baseline_hash", source_volume_id="vol-work", target_volume_id=None,
                              params={"mount": "/x"})
        repo.set_job_state(c, job, "done")
        repo.set_job_result(c, job, {"files": 1, "hashed": 1, "unreadable": 0})
    c.close()
    _, body = srv.call("GET", "/api/volumes/vol-work")
    assert body["runs"] == [{"job_id": job, "kind": "baseline_hash", "label": "Hash", "state": "done",
                             "started_at": body["runs"][0]["started_at"],
                             "finished_at": body["runs"][0]["finished_at"],
                             "result": {"files": 1, "hashed": 1, "unreadable": 0}}]


def test_reveal_opens_drive_or_file_in_finder(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(web.sys, "platform", "darwin")
    monkeypatch.setattr(web.subprocess, "run", fake_run)
    mount = (srv.root / "WORK").resolve()
    photo = srv.root / "WORK" / "Photos" / "DSC_0001.JPG"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")

    assert srv.call("POST", "/api/volumes/vol-work/reveal", {}) == (200, {"opened": str(mount)})
    assert calls[-1] == ["open", str(mount)]
    status, body = srv.call("POST", "/api/volumes/vol-work/reveal", {"rel_path": "Photos/DSC_0001.JPG"})
    assert status == 200 and calls[-1] == ["open", "-R", str(photo.resolve())]
    assert srv.call("POST", "/api/volumes/vol-work/reveal", {"rel_path": "../../etc"})[0] == 400
    assert srv.call("POST", "/api/volumes/vol-work/reveal", {"rel_path": "Photos/gone.JPG"})[0] == 409
    assert srv.call("POST", "/api/volumes/vol-leg/reveal", {})[0] == 409  # not plugged in
    assert srv.call("POST", "/api/volumes/nope/reveal", {})[0] == 404
    assert srv.call("GET", "/api/volumes/vol-work/reveal")[0] == 405

    monkeypatch.setattr(web.sys, "platform", "linux")
    assert srv.call("POST", "/api/volumes/vol-work/reveal", {})[0] == 400
    assert len(calls) == 2  # nothing ran for the refused requests


def test_eject_runs_diskutil_and_refuses_busy_or_unsafe_drives(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    """rv-43: Eject is Finder's eject; it is refused while a task uses the drive, and macOS's refusal is passed on."""
    calls: list[list[str]] = []
    outcome = {"code": 0, "err": ""}

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, outcome["code"], "", outcome["err"])

    procs: list = []
    monkeypatch.setattr(web.sys, "platform", "darwin")
    monkeypatch.setattr(web.subprocess, "run", fake_run)
    monkeypatch.setattr(web.activity, "list_processes", lambda: procs)
    mount = srv.root / "WORK"

    assert srv.call("POST", "/api/volumes/vol-work/eject", {}) == (200, {"ejected": str(mount), "label": "WORK"})
    assert calls[-1] == ["diskutil", "eject", str(mount)]

    outcome.update(code=1, err="Volume WORK failed to eject: in use by Finder")
    status, body = srv.call("POST", "/api/volumes/vol-work/eject", {})
    assert status == 409 and "in use by Finder" in body["error"]

    procs.append(activity.EngineProcess(pid=77, ppid=1, elapsed_s=5, words=["hash", str(mount)], text=""))
    n = len(calls)
    status, body = srv.call("POST", "/api/volumes/vol-work/eject", {})
    assert status == 409 and "pid 77" in body["error"]
    assert srv.call("POST", "/api/volumes/vol-leg/eject", {})[0] == 409  # not plugged in
    assert srv.call("POST", "/api/volumes/nope/eject", {})[0] == 404
    assert srv.call("GET", "/api/volumes/vol-work/eject")[0] == 405
    monkeypatch.setattr(web.sys, "platform", "linux")
    assert srv.call("POST", "/api/volumes/vol-work/eject", {})[0] == 400
    assert len(calls) == n  # nothing ran for the refused requests


def test_uses_mount_matches_the_drive_or_a_path_on_it() -> None:
    assert web._uses_mount(["hash", "/mnt/A"], "/mnt/A")
    assert web._uses_mount(["ingest", "--source", "/mnt/A/DCIM"], "/mnt/A/")
    assert not web._uses_mount(["hash", "/mnt/AB"], "/mnt/A")


def test_meta_scanned_flag_follows_group_meta(srv: Client) -> None:
    c = db.connect(srv.app.catalog_path)
    with repo.transaction(c):
        repo.ensure_group_meta(c, srv.ids["a"])  # the metadata scan wrote a row for the one photo on WORK
    c.close()
    by_label = {d["label"]: d for d in srv.call("GET", "/api/drives")[1]["drives"]}
    assert by_label["WORK"]["meta_scanned"] is True
    assert by_label["OLD"]["meta_scanned"] is False  # its photo is not scanned yet


# --------------------------------------------------------------------------- copies across drives (ui-8)


def seed_copies(srv: Client) -> dict[str, int]:
    """WORK holds X (twice), Y and Z, all verified. OLD also holds X; a CARD holds Y; Z is only on WORK."""
    c = db.connect(srv.app.catalog_path)
    with repo.transaction(c):
        repo.upsert_volume(c, volume_id="vol-card", role="CARD", label="CARD", fs_type="exfat",
                           da_volume_uuid="UUID-CARD", marker_id=None, capacity_bytes=10**9, mount_path=None)
        assets = {k: repo.insert_asset(c, sha256=k * 64, size=size, partial_sha256=k * 2, kind="photo",
                                       group_id=None, group_role=None)
                  for k, size in (("x", 100), ("y", 200), ("z", 300))}
        for vid, rel, key in (("vol-work", "A/X.NEF", "x"), ("vol-work", "B/X copy.NEF", "x"),
                              ("vol-work", "A/Y.NEF", "y"), ("vol-work", "A/Z.NEF", "z"),
                              ("vol-leg", "Old/X.NEF", "x"), ("vol-card", "DCIM/Y.NEF", "y")):
            repo.upsert_instance(c, volume_id=vid, rel_path=rel, size={"x": 100, "y": 200, "z": 300}[key],
                                 mtime_ns=1, kind="photo", state="verified", asset_id=assets[key])
    c.close()
    return assets


def test_volume_summary_counts_copies_elsewhere(srv: Client) -> None:
    seed_copies(srv)
    _, body = srv.call("GET", "/api/volumes/vol-work")
    copies = body["copies"]
    assert copies["hashed"] == 4  # the seeded `indexed` photo is not a verified copy
    assert copies["elsewhere"] == {"files": 2, "bytes": 200}  # both copies of X; Y's only other copy is a card
    assert copies["only_here"] == {"files": 2, "bytes": 500}
    assert copies["dup_here"] == {"assets": 1, "files": 2, "extra_bytes": 100}
    assert copies["by_volume"] == [{"volume_id": "vol-leg", "label": "OLD", "role": "LEGACY", "files": 2,
                                    "bytes": 200, "mounted": False}]

    _, old = srv.call("GET", "/api/volumes/vol-leg")
    assert old["copies"]["elsewhere"] == {"files": 1, "bytes": 100}
    assert old["copies"]["by_volume"][0]["label"] == "WORK" and old["copies"]["by_volume"][0]["files"] == 1


def test_file_list_marks_and_filters_copies(srv: Client) -> None:
    seed_copies(srv)

    def paths(qs: str) -> list[str]:
        status, body = srv.call("GET", f"/api/volumes/vol-work/files?{qs}")
        assert status == 200, body
        return [f["rel_path"] for f in body["files"]]

    _, body = srv.call("GET", "/api/volumes/vol-work/files")
    by_path = {f["rel_path"]: f for f in body["files"]}
    assert (by_path["A/X.NEF"]["copies"], by_path["A/X.NEF"]["dup_here"]) == (1, 1)
    assert (by_path["A/Y.NEF"]["copies"], by_path["A/Y.NEF"]["dup_here"]) == (0, 0)  # a card copy does not count
    assert by_path["A/X.NEF"]["asset_id"]
    assert paths("copies=elsewhere") == ["A/X.NEF", "B/X copy.NEF"]
    assert paths("copies=only_here") == ["A/Y.NEF", "A/Z.NEF"]
    assert paths("copies=dup_here") == ["A/X.NEF", "B/X copy.NEF"]
    assert paths("other=vol-leg") == ["A/X.NEF", "B/X copy.NEF"]
    assert paths("copies=only_here&state=verified") == ["A/Y.NEF", "A/Z.NEF"]
    assert srv.call("GET", "/api/volumes/vol-work/files?copies=bogus")[0] == 400
    assert srv.call("GET", "/api/volumes/vol-work/files?other=vol-work")[0] == 400
    assert srv.call("GET", "/api/volumes/vol-work/files?other=nope")[0] == 400


def test_file_list_totals_names_and_camera_filter(srv: Client) -> None:
    seed_copies(srv)
    c = db.connect(srv.app.catalog_path)
    with repo.transaction(c):
        body = repo.get_or_create_body(c, "Z 6_2", "", "Z6II")
        group = repo.insert_group(c, body_id=body, shutter_count=1, capture_time=None, capture_offset=None,
                                  time_source="exif", canonical_stem="s", original_stem="X", original_num=1,
                                  shoot_id=None)
        c.execute("UPDATE assets SET group_id = ? WHERE sha256 = ?", (group, "x" * 64))
    c.close()

    _, summary = srv.call("GET", "/api/volumes/vol-work")
    assert [(m["camera"], m["name"], m["files"]) for m in summary["cameras"]] == [
        (str(body), "Z6II", 2), ("unknown", "Unknown camera", summary["files"] - 2)]
    _, all_files = srv.call("GET", "/api/volumes/vol-work/files?limit=1")
    assert all_files["total"] == summary["files"] and len(all_files["files"]) == 1  # total spans every page
    _, cam = srv.call("GET", f"/api/volumes/vol-work/files?camera={body}")
    assert cam["total"] == 2 and {f["camera_name"] for f in cam["files"]} == {"Z6II"}
    assert cam["files"][0]["copy_drives"] == [{"label": "OLD", "role": "LEGACY"}]  # the other drive is named, not only counted
    _, unknown = srv.call("GET", "/api/volumes/vol-work/files?camera=unknown&copies=only_here")
    assert [f["rel_path"] for f in unknown["files"]] == ["A/Y.NEF", "A/Z.NEF"] and unknown["total"] == 2
    assert srv.call("GET", "/api/volumes/vol-work/files?camera=nikon")[0] == 400


def test_card_files_filter_by_copies(srv: Client) -> None:
    seed_card(srv)

    def names(qs: str) -> list[str]:
        status, body = srv.call("GET", f"/api/cards/vol-card/files?{qs}")
        assert status == 200, body
        return [f["rel_path"].rsplit("/", 1)[1] for f in body["files"]]

    assert names("copies=elsewhere") == ["P.NEF"]
    assert names("copies=only_here") == ["N.NEF"]
    assert names("copies=dup_here") == []
    assert names("other=vol-work") == ["P.NEF"]
    assert names("copies=elsewhere&tier=only_on_card") == []
    assert srv.call("GET", "/api/cards/vol-card/files?copies=bogus")[0] == 400
    assert srv.call("GET", "/api/cards/vol-card/files?other=nope")[0] == 400


def test_asset_copies_lists_every_drive(srv: Client) -> None:
    assets = seed_copies(srv)
    status, body = srv.call("GET", f"/api/assets/{assets['x']}/copies")
    assert status == 200 and body["size"] == 100
    assert [(c["label"], c["rel_path"], c["mounted"]) for c in body["copies"]] == [
        ("OLD", "Old/X.NEF", False), ("WORK", "A/X.NEF", True), ("WORK", "B/X copy.NEF", True)]
    _, y = srv.call("GET", f"/api/assets/{assets['y']}/copies")
    assert [c["role"] for c in y["copies"]] == ["WORKING", "CARD"]  # cards listed last, labelled by role
    assert srv.call("GET", "/api/assets/999999/copies")[0] == 404
    assert srv.call("POST", f"/api/assets/{assets['x']}/copies", {})[0] == 405


# --------------------------------------------------------------------------- cards (card-1, card-3)


def seed_card(srv: Client) -> None:
    """CARD1 (plugged in) holds P (also on WORK) and N (only on the card); both checked."""
    folder = srv.root / "CARD1" / "DCIM" / "100NZ6_2"
    folder.mkdir(parents=True)
    c = db.connect(srv.app.catalog_path)
    with repo.transaction(c):
        repo.upsert_volume(c, volume_id="vol-card", role="CARD", label="card:CARD1", fs_type="exfat",
                           da_volume_uuid="UUID-CARD1", marker_id=None, capacity_bytes=10**9,
                           mount_path=srv.root / "CARD1")
        for name, data, on_work in (("P.NEF", b"p" * 50, True), ("N.NEF", b"n" * 70, False)):
            f = folder / name
            f.write_bytes(data)
            st = f.stat()
            asset = repo.insert_asset(c, sha256=name[0] * 64, size=st.st_size, partial_sha256=name[0] * 2,
                                      kind="raw", group_id=None, group_role=None)
            repo.upsert_instance(c, volume_id="vol-card", rel_path=f"DCIM/100NZ6_2/{name}", size=st.st_size,
                                 mtime_ns=st.st_mtime_ns, kind="raw", state="verified", asset_id=asset)
            if on_work:
                repo.upsert_instance(c, volume_id="vol-work", rel_path=f"Photos/{name}", size=st.st_size,
                                     mtime_ns=1, kind="raw", state="verified", asset_id=asset)
    c.close()


def test_cards_list_and_ranked_files(srv: Client) -> None:
    seed_card(srv)
    status, body = srv.call("GET", "/api/cards")
    assert status == 200
    [card] = body["cards"]
    assert card["volume_id"] == "vol-card" and card["mounted"] is True and card["tier"] == "only_on_card"
    assert card["counts"]["needs_backup"]["files"] == 1 and card["counts"]["only_on_card"]["files"] == 1
    assert [c["tier"] for c in body["cameras"]] == ["only_on_card"]

    status, body = srv.call("GET", "/api/cards/vol-card/files")
    assert status == 200 and body["total"] == 2
    assert [(f["rel_path"].rsplit("/", 1)[1], f["tier"]) for f in body["files"]] == [
        ("N.NEF", "only_on_card"), ("P.NEF", "needs_backup")]  # most urgent first
    assert body["files"][1]["copies"] == [{"role": "WORKING", "label": "WORK", "volume_id": "vol-work"}]
    assert body["files"][1]["reason"] == "On WORK; needs a second working or backup drive"  # names the drive
    assert body["card"]["copies_by_volume"] == [{"volume_id": "vol-work", "label": "WORK", "role": "WORKING",
                                                 "files": 1, "bytes": 50}]
    assert body["card"]["tier"] == "only_on_card"
    _, only = srv.call("GET", "/api/cards/vol-card/files?tier=needs_backup")
    assert [f["tier"] for f in only["files"]] == ["needs_backup"]
    assert srv.call("GET", "/api/cards/vol-card/files?tier=bogus")[0] == 400
    assert srv.call("GET", "/api/cards/vol-work/files")[0] == 404  # not a card
    assert srv.call("POST", "/api/cards", {})[0] == 405


def test_card_make_green_plan_endpoint(srv: Client) -> None:
    seed_card(srv)
    status, body = srv.call("GET", "/api/cards/vol-card/make-green")
    assert status == 200 and body["files"] == 1 and body["possible"] is False
    [src] = body["sources"]
    assert src["source"]["label"] == "WORK" and src["ok"] is False and "second working drive" in src["reason"]
    assert srv.call("GET", "/api/cards/vol-work/make-green")[0] == 404          # not a card
    assert srv.call("POST", "/api/cards/vol-card/make-green", {})[0] == 405
    bad = srv.call("POST", "/api/run", {"cmd": "backup", "args": {"card": "c", "make_green": True, "target": "/b"}})
    assert bad[0] == 400


def test_mac_folder_setting_and_drive(srv: Client) -> None:
    assert srv.call("POST", "/api/settings", {"mac_folder": "~/Nope"})[0] == 400  # must exist
    assert srv.call("POST", "/api/settings", {"mac_folder": "~"})[0] == 400  # not home itself
    assert srv.call("POST", "/api/settings", {"mac_folder": str(srv.root)})[0] == 400  # outside home
    sandbox = Path.home() / "Sandbox"
    sandbox.mkdir()
    status, body = srv.call("POST", "/api/settings", {"mac_folder": "~/Sandbox"})
    assert status == 200 and body["settings"]["mac_folder"] == "~/Sandbox"
    mac = [d for d in srv.call("GET", "/api/drives")[1]["drives"] if d["local"]]
    assert [(d["label"], d["mount"], d["mounted"]) for d in mac] == [("This Mac", str(sandbox), True)]


def test_every_built_command_is_allowed_by_run(srv: Client) -> None:
    """`/api/run` has its own allow-list; a command `build_argv` knows must pass it (the card_check bug)."""
    status, body = srv.call("POST", "/api/run", {"cmd": "card_check", "args": {"mount": "/c"}})
    assert status == 200 and isinstance(body["task_id"], int), body
    import inspect
    built = set(re.findall(r'cmd == "(\w+)"|"(\w+)": "', inspect.getsource(web.build_argv)))
    names = {a or b for a, b in built} - {"card-status", "index", "hash"}
    assert names | {"card_status", "index", "hash"} <= set(web.ALLOWED_CMDS)


# --------------------------------------------------------------------------- runs started elsewhere (ui-11)

def test_activity_lists_outside_runs_and_stale_jobs(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from photovault import activity

    conn = db.connect(load_config().catalog_path)
    live = repo.create_job(conn, kind="index_volume", source_volume_id="vol-leg", target_volume_id=None,
                           params={"mount": "/Volumes/Big Drive"})
    stale = repo.create_job(conn, kind="baseline_hash", source_volume_id="vol-leg", target_volume_id=None,
                            params={"mount": "/Volumes/PP 1"})
    for job_id in (live, stale):
        repo.set_job_state(conn, job_id, "running")
    conn.close()
    text = "--json index /Volumes/Big Drive"
    procs = [activity.EngineProcess(pid=4242, ppid=1, elapsed_s=90, words=activity.guess_words(text), text=text),
             activity.EngineProcess(pid=4243, ppid=os.getpid(), elapsed_s=5, words=["hash", "/x"], text="hash /x")]
    monkeypatch.setattr(activity, "list_processes", lambda: procs)
    status, body = srv.call("GET", "/api/activity")
    assert status == 200
    assert [(a["pid"], a["words"], a["job"]["job_id"]) for a in body["activity"]] == [
        (4242, ["index", "/Volumes/Big Drive"], live)]
    assert body["stale_jobs"] == []  # idx-9: the ownerless job was paused instead
    conn = db.connect(load_config().catalog_path)
    assert repo.get_job(conn, stale)["state"] == "paused" and repo.get_job(conn, live)["state"] == "running"
    conn.close()


# --------------------------------------------------------------------------- keep the Mac awake (ui-12)

def test_runner_keeps_the_mac_awake_until_the_task_exits(tmp_path: Path) -> None:
    seen = tmp_path / "awake.txt"
    awake = [sys.executable, "-c", f"import sys; open({str(seen)!r}, 'w').write(' '.join(sys.argv[1:]))", "-w"]
    runner = web.TaskRunner([sys.executable, "-c", CHILD], awake_argv=awake)
    task_id = runner.start(["index", "/x"])
    deadline = time.time() + 10
    while (runner.get(task_id).state == "running" or not seen.exists()) and time.time() < deadline:
        time.sleep(0.05)
    assert runner.get(task_id).summary()["keep_awake"] is True
    assert re.fullmatch(r"-w \d+", seen.read_text())


def test_task_keeps_its_job_id_after_the_event_leaves_the_window() -> None:
    """rv-31: an import reports its job id once, early; the UI nests a --follow copy under it by that id."""
    task = web._Task(1, ["ingest"])
    assert task.summary()["job_id"] is None
    task.add({"event": "result", "job_id": 41, "planned": 10})
    for i in range(web.EVENTS_KEPT + 5):
        task.add({"event": "progress", "current": i})
    task.add({"event": "result", "job_id": 99})  # a later result does not replace the first
    assert all(e.get("job_id") != 41 for e in task.events)
    assert task.summary()["job_id"] == 41
    assert task.detail(-1)["job_id"] == 41


def test_task_remembers_the_make_green_route() -> None:
    """rv-36: make-green names its drive pair only in a progress message; copy progress follows within seconds."""
    task = web._Task(1, ["backup", "--card", "c1", "--make-green"])
    assert task.summary()["route"] is None
    task.add({"event": "progress", "phase": "make_green", "message": "copying 9 file(s) from SSD to BAK"})
    for i in range(web.EVENTS_KEPT + 5):
        task.add({"event": "progress", "phase": "copy", "current": i, "message": "1 GB copied, 50 MB/s, 0 failed"})
    assert task.summary()["route"] == "copying 9 file(s) from SSD to BAK"


def test_runner_awake_defaults() -> None:
    assert web.TaskRunner([sys.executable]).awake_argv is None  # custom engine (tests): off
    assert web.TaskRunner(awake_argv=None).awake_argv is None
    assert web.TaskRunner().awake_argv == web.default_awake_argv()


def test_label_renames_a_drive_in_the_catalog_only(srv: Client) -> None:
    before = sorted(p.name for p in (srv.root / "WORK").iterdir()) if (srv.root / "WORK").exists() else []
    assert srv.call("POST", "/api/volumes/vol-work/label", {"label": "  Field   SSD "}) == (
        200, {"volume_id": "vol-work", "label": "Field SSD"})
    _, body = srv.call("GET", "/api/drives")
    assert [d["label"] for d in body["drives"] if d["volume_id"] == "vol-work"] == ["Field SSD"]
    # The drive itself is untouched: same folder, same contents.
    after = sorted(p.name for p in (srv.root / "WORK").iterdir()) if (srv.root / "WORK").exists() else []
    assert after == before

    assert srv.call("POST", "/api/volumes/vol-work/label", {"label": "   "})[0] == 400
    assert srv.call("POST", "/api/volumes/vol-work/label", {"label": "x" * 65})[0] == 400
    assert srv.call("POST", "/api/volumes/vol-work/label", {"label": 3})[0] == 400
    assert srv.call("POST", "/api/volumes/vol-work/label", {"label": "a\x07b"})[0] == 400
    assert srv.call("POST", "/api/volumes/nope/label", {"label": "X"})[0] == 404
    assert srv.call("GET", "/api/volumes/vol-work/label")[0] == 405
    _, body = srv.call("GET", "/api/volumes/vol-work")
    assert body["drive"]["label"] == "Field SSD"


def test_clock_suggestions_endpoint(srv: Client) -> None:
    from tests.test_clock import frames, phone

    assert srv.call("GET", "/api/clock-suggestions") == (200, {"suggestions": []})
    conn = db.connect(srv.app.catalog_path)
    try:
        phone(conn)
        _, ids = frames(conn, "Zed", off_h=8)
        conn.commit()
    finally:
        conn.close()
    status, body = srv.call("GET", "/api/clock-suggestions")
    assert status == 200 and len(body["suggestions"]) == 1
    s = body["suggestions"][0]
    assert s["body"] == "Zed" and s["delta_s"] == -8 * 3600
    group = s["groups"][0]
    assert group["time_shift_s"] == -8 * 3600 and set(group["group_ids"]) <= set(ids)
    # Apply writes a body_clock row (sh-0b), not per-frame shifts
    status, made = srv.call("POST", "/api/body-clock", {
        "body_id": s["body_id"], "start_utc": s["start_utc"], "end_utc": s["end_utc"],
        "correction_s": s["delta_s"], "source": "phone"})
    assert status == 200
    rows = srv.call("GET", "/api/body-clock")[1]["rows"]
    assert [r["clock_id"] for r in rows] == [made["clock_id"]] and rows[0]["body"] == "Zed" and rows[0]["accepted"] == 1
    assert srv.call("GET", "/api/clock-suggestions") == (200, {"suggestions": []})
    listed = {g["group_id"]: g for g in srv.call("GET", "/api/groups")[1]["groups"]}
    assert listed[group["group_ids"][0]]["time_provenance"] == "aligned"
    # undo
    assert srv.call("POST", f"/api/body-clock/{made['clock_id']}/delete")[0] == 200
    assert srv.call("GET", "/api/body-clock")[1]["rows"] == []
    assert srv.call("POST", f"/api/body-clock/{made['clock_id']}/delete")[0] == 404
    assert len(srv.call("GET", "/api/clock-suggestions")[1]["suggestions"]) == 1


def test_body_clock_validates(srv: Client) -> None:
    ok = {"body_id": 1, "start_utc": "2026-07-12T00:00:00+00:00", "end_utc": "2026-07-12T01:00:00+00:00",
          "correction_s": 60}
    assert srv.call("POST", "/api/body-clock", ok)[0] == 400  # no such body
    for bad in ({"correction_s": "x"}, {"start_utc": "nope"}, {"source": "other"},
                {"start_utc": "2026-07-13T00:00:00+00:00"}):
        assert srv.call("POST", "/api/body-clock", {**ok, **bad})[0] == 400


def test_groups_carry_the_two_times(srv: Client) -> None:
    a = {g["group_id"]: g for g in srv.call("GET", "/api/groups")[1]["groups"]}[srv.ids["a"]]
    assert a["time_provenance"] == "assumed_offset" and a["wall_time"] == "2026-11-10T21:00:00"
    assert a["wall_date"] == "2026-11-10" and a["time_user_shift"] is False


# --------------------------------------------------------------------------- vault name and viewer preview (rv-18, rv-20)

def test_groups_carry_the_vault_name(srv: Client) -> None:
    rows = {g["group_id"]: g for g in srv.call("GET", "/api/groups")[1]["groups"]}
    # not yet imported (no shoot): the D-005 name ingest would give; no sub-second stored, so "00"
    assert rows[srv.ids["a"]]["vault_name"] == "M0001_20261110-210000-00_UNKNOWN_0000001.JPG"
    assert rows[srv.ids["b"]]["vault_name"] is None  # no file located


def test_vault_name_uses_the_stored_stem_once_imported(srv: Client) -> None:
    from photovault import naming
    from photovault.config import load_config
    row = {"rel_path": "Photos/DSC_0001.nef", "canonical_stem": "M0001_X", "shoot_id": 3}
    assert naming.vault_name(load_config(), row) == "M0001_X.NEF"


def test_preview_prefers_the_large_jpeg_and_caches(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = srv.root / "WORK" / "Photos" / "DSC_0001.JPG"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    seen = []

    def fake_preview(path: Path, tags: tuple = ()) -> bytes:
        seen.append(tags)
        return b"\xff\xd8\xff\xd9big"

    monkeypatch.setattr(preview, "extract_preview", fake_preview)
    gid = srv.ids["a"]
    assert srv.call("GET", f"/api/groups/{gid}/preview") == (200, b"\xff\xd8\xff\xd9big")
    assert seen == [("JpgFromRaw", "PreviewImage")]
    assert (srv.home / "previews" / f"{gid}.jpg").is_file()
    assert srv.call("GET", f"/api/groups/{gid}/preview")[0] == 200 and len(seen) == 1
    assert srv.call("GET", f"/api/groups/{srv.ids['c']}/preview")[0] == 404  # drive not mounted


# --------------------------------------------------------------------------- group detail (rv-21)

def _fake_read(calls: list):
    def fake(paths, **kw):
        calls.append(list(paths))
        res = metadata.MetadataResult()
        res.meta = {Path(p): {"ExposureTime": 0.004, "FNumber": 2.8, "ISO": 3200, "FocalLength": 24.0,
                              "LensModel": "NIKKOR Z 24-70mm f/2.8 S"} for p in paths}
        return res
    return fake


def test_group_detail_instances_preview_from_and_exif_cache(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = srv.root / "WORK" / "Photos" / "DSC_0001.JPG"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    conn = db.connect(srv.app.catalog_path)
    with repo.transaction(conn):
        asset = conn.execute("SELECT asset_id FROM assets WHERE group_id = ?", (srv.ids["a"],)).fetchone()[0]
        repo.upsert_instance(conn, volume_id="vol-leg", rel_path="Old/DSC_0001.JPG", size=10, mtime_ns=1,
                             kind="photo", state="verified", asset_id=asset)
    conn.close()
    calls: list = []
    monkeypatch.setattr(metadata, "read_metadata", _fake_read(calls))
    status, body = srv.call("GET", f"/api/groups/{srv.ids['a']}/detail")
    assert status == 200
    by_vol = {i["volume_id"]: i for i in body["instances"]}
    assert by_vol["vol-work"]["mounted"] is True and by_vol["vol-work"]["label"] == "WORK"
    assert by_vol["vol-leg"]["mounted"] is False and by_vol["vol-leg"]["state"] == "verified"
    assert body["preview_from"] == "vol-work"
    assert body["exif"] == {"exposure_s": 0.004, "fnumber": 2.8, "iso": 3200, "focal_mm": 24.0,
                            "lens": "NIKKOR Z 24-70mm f/2.8 S"}
    assert (srv.home / "exif" / f"{srv.ids['a']}.json").is_file()
    assert srv.call("GET", f"/api/groups/{srv.ids['a']}/detail")[1]["exif"] == body["exif"]
    assert len(calls) == 1  # second request came from the cache


def test_group_detail_without_a_mounted_drive(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(metadata, "read_metadata", lambda *a, **k: pytest.fail("must not read"))
    status, body = srv.call("GET", f"/api/groups/{srv.ids['c']}/detail")
    assert status == 200 and body["preview_from"] is None and body["exif"] is None
    assert [(i["volume_id"], i["mounted"]) for i in body["instances"]] == [("vol-leg", False)]
    assert srv.call("GET", "/api/groups/99999/detail")[0] == 404


# --------------------------------------------------------------------------- full size and availability (rv-23)

def _get_full(srv: Client, gid: int):
    req = urllib.request.Request(f"http://127.0.0.1:{srv.port}/api/groups/{gid}/preview?full=1")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read()), dict(exc.headers)


def _jpeg(w: int, h: int) -> bytes:
    import io

    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h)).save(buf, "JPEG")
    return buf.getvalue()


def test_full_preview_serves_a_jpeg_primary_as_the_file(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = srv.root / "WORK" / "Photos" / "DSC_0001.JPG"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(_jpeg(60, 40))
    monkeypatch.setattr(preview, "extract_preview", lambda *a, **k: pytest.fail("must not run exiftool"))
    status, body, h = _get_full(srv, srv.ids["a"])
    assert status == 200 and body == photo.read_bytes()
    assert (h["X-Image-Width"], h["X-Image-Height"], h["X-Image-Full"]) == ("60", "40", "1")


def test_full_preview_of_a_raw_uses_jpgfromraw_and_caches(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = srv.root / "WORK" / "Photos" / "DSC_0001.NEF"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"raw")
    conn = db.connect(srv.app.catalog_path)
    with repo.transaction(conn):
        conn.execute("UPDATE instances SET rel_path = 'Photos/DSC_0001.NEF' WHERE rel_path = 'Photos/DSC_0001.JPG'")
    conn.close()
    seen = []

    def fake(path: Path, tags: tuple = ()) -> bytes | None:
        seen.append(tags)
        return _jpeg(90, 60) if tags == ("JpgFromRaw",) else None

    monkeypatch.setattr(preview, "extract_preview", fake)
    status, body, h = _get_full(srv, srv.ids["a"])
    assert status == 200 and (h["X-Image-Width"], h["X-Image-Full"]) == ("90", "1") and seen == [("JpgFromRaw",)]
    # the file is no longer reachable: the cached full image still serves
    photo.unlink()
    assert _get_full(srv, srv.ids["a"])[0] == 200 and len(seen) == 1
    # the default size is a separate cache
    assert not (srv.home / "previews" / f"{srv.ids['a']}.jpg").exists()


def test_full_preview_409_lists_the_drives_to_plug_in(srv: Client) -> None:
    status, body, _ = _get_full(srv, srv.ids["c"])
    assert status == 409 and [n["volume_id"] for n in body["needs"]] == ["vol-leg"]
    assert set(body["needs"][0]) == {"volume_id", "label", "role"}
    assert _get_full(srv, 99999)[0] == 404


def test_groups_say_whether_full_size_is_available(srv: Client) -> None:
    rows = {g["group_id"]: g for g in srv.call("GET", "/api/groups")[1]["groups"]}
    assert rows[srv.ids["a"]]["full_available"] is True and rows[srv.ids["a"]]["held_on"] == ["WORK"]
    assert rows[srv.ids["c"]]["full_available"] is False and len(rows[srv.ids["c"]]["held_on"]) == 1
    assert rows[srv.ids["b"]]["full_available"] is False and rows[srv.ids["b"]]["held_on"] == []


def test_shoots_plan_newest_file_wins_404_and_dev_override(srv: Client, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("PHOTOVAULT_DEV_PLAN", raising=False)
    assert srv.call("GET", "/api/shoots/plan") == (404, {"error": "no plan yet"})
    plans = srv.home / "plans"
    plans.mkdir(parents=True)
    (plans / "shoots-20261101-100000.json").write_text(json.dumps({"plan_id": "old"}))
    (plans / "shoots-20261118-210000.json").write_text(json.dumps({"plan_id": "new"}))
    (plans / "notes.json").write_text("{}")
    status, body = srv.call("GET", "/api/shoots/plan")
    assert status == 200 and body["plan_id"] == "new"
    dev = tmp_path / "dev.json"
    dev.write_text(json.dumps({"plan_id": "dev"}))
    monkeypatch.setenv("PHOTOVAULT_DEV_PLAN", str(dev))
    assert srv.call("GET", "/api/shoots/plan")[1]["plan_id"] == "dev"
    monkeypatch.setenv("PHOTOVAULT_DEV_PLAN", str(tmp_path / "missing.json"))
    assert srv.call("GET", "/api/shoots/plan")[0] == 404


def test_vault_name_uses_the_stored_subsec(srv: Client) -> None:
    from photovault import naming
    row = {"rel_path": "Photos/DSC_0001.nef", "canonical_stem": "x", "shoot_id": None, "capture_time": "2026-11-10T21:00:00",
           "shutter_count": 1, "original_stem": "DSC_0001", "original_num": 1, "subsec": "81"}
    assert naming.vault_name(load_config(), row) == "M0001_20261110-210000-81_UNKNOWN_0000001.NEF"
    assert naming.vault_name(load_config(), {**row, "subsec": None}).endswith("-00_UNKNOWN_0000001.NEF")


def test_file_photo_finds_the_group_and_day(srv: Client) -> None:
    """rv-27: Show in Photos resolves a drive file to its photo, hashed (asset) or only indexed (job item)."""
    conn = db.connect(srv.app.catalog_path)
    try:
        gid_a = conn.execute("SELECT group_id FROM capture_groups WHERE original_stem = 'DSC_0001'").fetchone()[0]
        with repo.transaction(conn):
            gid = repo.insert_group(conn, body_id=None, shutter_count=9, capture_time="2026-07-01T10:00:00",
                                    capture_offset=None, time_source="exif", canonical_stem="DSC_0009",
                                    original_stem="DSC_0009", original_num=None, shoot_id=None)
            repo.upsert_instance(conn, volume_id="vol-work", rel_path="Old/DSC_0009.NEF", size=1, mtime_ns=1,
                                 kind="raw", state="indexed")
            job = repo.create_job(conn, kind="index_volume", source_volume_id="vol-work", target_volume_id=None,
                                  params={})
            repo.add_job_items(conn, job, [{"seq": 1, "src_rel": "Old/DSC_0009.NEF", "dst_rel": None,
                                            "group_id": gid, "role": "primary"}])
    finally:
        conn.close()
    base = "/api/volumes/vol-work/photo?rel_path="
    assert srv.call("GET", base + "Photos/DSC_0001.JPG") == (200, {"group_id": gid_a, "day": "2026-11-10"})
    assert srv.call("GET", base + "photos/dsc_0001.jpg")[1]["group_id"] == gid_a  # exFAT: case-insensitive key
    assert srv.call("GET", base + "Old/DSC_0009.NEF") == (200, {"group_id": gid, "day": "2026-07-01"})
    assert srv.call("GET", base + "Nope.JPG")[0] == 404
    assert srv.call("GET", "/api/volumes/vol-work/photo")[0] == 400
    assert srv.call("GET", "/api/volumes/nope/photo?rel_path=x")[0] == 404


def test_card_detail_carries_the_test_card_badge(srv: Client) -> None:
    """tc-1: the card page reads its header from the files endpoint, so it must say which drives are hidden."""
    from photovault import testcard
    seed_card(srv)
    assert srv.call("GET", "/api/cards/vol-card/files")[1]["card"]["test_card"] is None
    c = db.connect(srv.app.catalog_path)
    label = repo.get_volume(c, "vol-work")["label"]
    testcard.mark(c, "vol-card", ["vol-work"])
    status, body = srv.call("GET", "/api/cards/vol-card/files")
    assert status == 200 and body["card"]["test_card"] == {"hidden": [label]}
    assert body["card"]["tier"] == "only_on_card"


def test_generation_poll_answers_without_holding_a_connection(srv: Client) -> None:
    """The UI polls this instead of /api/events, so tabs no longer use up the browser's connections to the host."""
    status, body = srv.call("GET", "/api/generation")
    assert status == 200 and isinstance(body["generation"], int)
