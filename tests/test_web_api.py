"""Local web API (ui-4): host guard, whitelist, drives, tasks, meta round-trip, suggestions, thumbnails.

Everything runs on tmp_path roots and a tmp catalog through a real server thread on port 0. Never /Volumes.
"""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

import pytest

from photovault import cli, preview, volumes
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

    def call(self, method: str, path: str, body=None, *, host: str | None = None, raw: bytes | None = None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data, method=method)
        req.add_header("Content-Type", "application/json")
        if host is not None:
            req.add_header("Host", host)
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
    client.root, client.ids, client.home = root, ids, home
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
                         "looks_like_card", "last_seen_at"}
    # mounted first
    assert [d["mounted"] for d in drives] == sorted([d["mounted"] for d in drives], reverse=True)


def test_drives_shows_unregistered_card(srv: Client) -> None:
    drives = {d["mount"]: d for d in srv.call("GET", "/api/drives")[1]["drives"] if d["mount"]}
    card = drives[str(srv.root / "CARD1")]
    assert card["role"] == "CARD (new)" and card["looks_like_card"] is True and card["volume_id"] is None


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
    ("ingest", {"source": "/c", "target": "/t", "name": "Night", "dry_run": True},
     ["ingest", "--source", "/c", "--target", "/t", "--name", "Night", "--dry-run"]),
    ("resume", {"job_id": 3}, ["resume", "3"]),
    ("card_status", {"mount": "/c"}, ["card-status", "/c"]),
    ("index", {"mount": "/w"}, ["index", "/w"]),
    ("hash", {"mount": "/w"}, ["hash", "/w"]),
    ("meta_scan", {"mount": "/w"}, ["meta", "scan", "--volume", "/w"]),
    ("meta_refs", {"folder": "/f", "label": "phone"}, ["meta", "refs", "--folder", "/f", "--label", "phone"]),
    ("meta_sidecars", {"mount": "/w", "dry_run": True}, ["meta", "sidecars", "--volume", "/w", "--dry-run"]),
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
              "gps_source": "map", "place_name": "Brighton"}
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

    # keyword removal (case-insensitive) through the same endpoint
    srv.call("POST", "/api/groups/meta", {"group_ids": [a], "fields": {"keywords_remove": ["BEACH"]}})
    row = next(g for g in srv.call("GET", "/api/groups")[1]["groups"] if g["group_id"] == a)
    assert row["keywords"] == ["sun"]


def test_groups_meta_validation(srv: Client) -> None:
    a = srv.ids["a"]
    post = lambda body: srv.call("POST", "/api/groups/meta", body)[0]  # noqa: E731
    assert post({"group_ids": [a], "fields": {"bogus": 1}}) == 400      # unknown field (ValueError)
    assert post({"group_ids": [999], "fields": {"creator": "x"}}) == 400  # unknown group
    assert post({"group_ids": [], "fields": {"creator": "x"}}) == 400
    assert post({"group_ids": [a], "fields": {}}) == 400
    assert post({"group_ids": [a], "fields": {"gps_source": "guess"}}) == 400
    assert post({"group_ids": [a], "fields": {"lat": "51"}}) == 400
    assert srv.call("GET", "/api/groups?missing=bogus")[0] == 400
    assert srv.call("GET", "/api/groups?limit=abc")[0] == 400


def test_suggestions_and_unknown_group(srv: Client) -> None:
    status, body = srv.call("GET", f"/api/groups/{srv.ids['a']}/suggestions")
    assert status == 200
    first = body["suggestions"][0]
    assert first["source"] == "phone" and first["delta_s"] == 300 and first["lat"] == pytest.approx(51.6)
    assert set(first) >= {"lat", "lon", "alt", "source", "source_ref", "delta_s", "confidence"}
    assert srv.call("GET", "/api/groups/999/suggestions")[0] == 404


# --------------------------------------------------------------------------- settings

def test_settings_defaults_and_updates(srv: Client) -> None:
    status, body = srv.call("GET", "/api/settings")
    assert body["settings"] == {"creator": "", "copyright": "", "default_offset": "+00:00", "match_window_s": 1800}
    status, body = srv.call("POST", "/api/settings", {"creator": " Mush ", "match_window_s": 600,
                                                      "default_offset": "+01:00"})
    assert status == 200
    assert body["settings"]["creator"] == "Mush" and body["settings"]["match_window_s"] == 600
    assert body["settings"]["default_offset"] == "+01:00"
    assert srv.call("POST", "/api/settings", {"unknown_key": 1})[0] == 400
    assert srv.call("POST", "/api/settings", {"match_window_s": 99999})[0] == 400
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


def test_thumb_404_when_no_preview(srv: Client, monkeypatch: pytest.MonkeyPatch) -> None:
    photo = srv.root / "WORK" / "Photos" / "DSC_0001.JPG"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    monkeypatch.setattr(preview, "extract_preview", lambda path: None)
    assert srv.call("GET", f"/api/groups/{srv.ids['a']}/thumb")[0] == 404
