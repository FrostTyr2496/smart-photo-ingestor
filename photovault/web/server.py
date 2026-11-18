"""Local web server for the PhotoVault UI (ui-4): a JSON API and static files, bound to 127.0.0.1 only.

- Every request must carry `Host: 127.0.0.1:<port>` or `Host: localhost:<port>` (DNS-rebinding guard).
- One catalog connection per request. Reads and small writes go straight to `catalog.repo`.
- Long engine commands run as subprocesses of the CLI (`python -m photovault --json ...`). `TaskRunner` keeps
  their parsed JSON lines in memory; Pause sends SIGINT, which the engine answers by pausing its job.
- Thumbnails are read-only: the embedded preview of a file on a drive that is mounted right now, cached under
  the PhotoVault home (never under a mount).
"""

from __future__ import annotations

import json
import logging
import mimetypes
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
from collections import deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from photovault import preview, volumes
from photovault.catalog import db, repo
from photovault.drives import VOLUMES_ROOT, scan_mounts
from photovault.geo import offset_tz, suggest
from photovault.protocol import EXIT_PAUSED, human_bytes

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
ROLE_CHOICES = ("WORKING", "MASTER", "LEGACY")
SETTING_DEFAULTS = {"creator": "", "copyright": "", "default_offset": "+00:00", "match_window_s": "1800"}
GPS_SOURCES = ("match", "map", "search", "neighbour")
EVENTS_KEPT = 500
MAX_BODY = 1_000_000
ALLOWED_HOSTS_FMT = ("127.0.0.1:{port}", "localhost:{port}")


class BadRequest(Exception):
    """400: the request is malformed or asks for something not allowed."""


class NotFound(Exception):
    """404."""


class Conflict(Exception):
    """409: the action does not fit the current state (e.g. pausing a task that is not running)."""


@dataclass(frozen=True)
class Raw:
    """A non-JSON response body (thumbnails)."""

    status: int
    content_type: str
    body: bytes


# --------------------------------------------------------------------------- tasks

def default_base_argv() -> list[str]:
    return [sys.executable, "-m", "photovault", "--json"]


def parse_line(line: str) -> dict:
    """One stdout line of the engine: its JSON event, or `{"event": "text", "line": ...}` for anything else."""
    text = line.rstrip("\r\n")
    try:
        value = json.loads(text)
    except ValueError:
        return {"event": "text", "line": text}
    if isinstance(value, dict) and isinstance(value.get("event"), str):
        return value
    return {"event": "text", "line": text}


class _Task:
    def __init__(self, task_id: int, argv: list[str]) -> None:
        self.task_id = task_id
        self.argv = argv
        self.state = "running"
        self.exit_code: int | None = None
        self.events: deque[dict] = deque(maxlen=EVENTS_KEPT)
        self.next_seq = 0

    def add(self, event: dict) -> None:
        event = {**event, "seq": self.next_seq}
        self.next_seq += 1
        self.events.append(event)

    def finish(self, state: str, exit_code: int | None) -> None:
        self.state = state
        self.exit_code = exit_code

    def summary(self) -> dict:
        return {"task_id": self.task_id, "state": self.state, "exit_code": self.exit_code, "argv": self.argv,
                "last_seq": self.next_seq - 1}

    def detail(self, since: int) -> dict:
        events = [e for e in self.events if e["seq"] > since]
        progress = [e for e in self.events if e.get("event") == "progress"]
        return {**self.summary(), "events": events, "latest_progress": progress[-1] if progress else None}


class TaskRunner:
    """Runs engine commands as subprocesses and keeps their events in memory (for the life of the server)."""

    def __init__(self, base_argv: list[str] | None = None) -> None:
        self.base_argv = list(base_argv) if base_argv is not None else default_base_argv()
        self._lock = threading.Lock()
        self._tasks: dict[int, _Task] = {}
        self._procs: dict[int, subprocess.Popen] = {}
        self._next_id = 1

    def start(self, args: list[str]) -> int:
        argv = [*self.base_argv, *args]
        with self._lock:
            task_id = self._next_id
            self._next_id += 1
            task = _Task(task_id, argv)
            self._tasks[task_id] = task
        try:
            proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                    bufsize=1)
        except OSError as exc:
            task.add({"event": "error", "code": "spawn", "message": str(exc)})
            task.finish("failed", None)
            return task_id
        with self._lock:
            self._procs[task_id] = proc
        threading.Thread(target=self._pump, args=(task, proc), daemon=True, name=f"task-{task_id}").start()
        return task_id

    def _pump(self, task: _Task, proc: subprocess.Popen) -> None:
        try:
            for line in proc.stdout:
                if line.strip():
                    task.add(parse_line(line))
        finally:
            code = proc.wait()
            with self._lock:
                self._procs.pop(task.task_id, None)
            if code == 0:
                state = "done"
            elif code == EXIT_PAUSED:
                state = "paused"
            else:
                state = "failed"
            task.finish(state, code)

    def pause(self, task_id: int) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            proc = self._procs.get(task_id)
        if task is None:
            raise NotFound(f"no task {task_id}")
        if proc is None or task.state != "running":
            raise Conflict(f"task {task_id} is not running")
        try:
            proc.send_signal(signal.SIGINT)
        except ProcessLookupError as exc:
            raise Conflict(f"task {task_id} already exited") from exc
        return task.summary()

    def get(self, task_id: int) -> _Task:
        with self._lock:
            task = self._tasks.get(task_id)
        if task is None:
            raise NotFound(f"no task {task_id}")
        return task

    def summaries(self) -> list[dict]:
        with self._lock:
            tasks = list(self._tasks.values())
        return [t.summary() for t in sorted(tasks, key=lambda t: t.task_id, reverse=True)]


def _text_arg(args: dict, key: str, *, required: bool = True) -> str | None:
    """A string argument that cannot be read as a flag: no leading '-', no NUL or newline."""
    value = args.get(key)
    if value is None or value == "":
        if required:
            raise BadRequest(f"missing argument {key!r}")
        return None
    if not isinstance(value, str):
        raise BadRequest(f"{key!r} must be a string")
    if value.startswith("-"):
        raise BadRequest(f"{key!r} may not start with '-'")
    if "\x00" in value or "\n" in value or "\r" in value:
        raise BadRequest(f"{key!r} contains a control character")
    return value


def _path_arg(args: dict, key: str) -> str:
    value = _text_arg(args, key)
    if not value.startswith("/"):
        raise BadRequest(f"{key!r} must be an absolute path")
    return value


def _bool_arg(args: dict, key: str) -> bool:
    value = args.get(key, False)
    if not isinstance(value, bool):
        raise BadRequest(f"{key!r} must be true or false")
    return value


def _allow_only(args: dict, allowed: set[str]) -> None:
    extra = set(args) - allowed
    if extra:
        raise BadRequest(f"unknown argument(s): {', '.join(sorted(extra))}")


def build_argv(cmd: str, args: dict) -> list[str]:
    """Map a whitelisted web command and its JSON arguments to CLI words. Anything else is a 400."""
    if cmd == "assign":
        _allow_only(args, {"mount", "role", "label"})
        mount = _path_arg(args, "mount")
        role = _text_arg(args, "role")
        if role not in ROLE_CHOICES:
            raise BadRequest(f"role must be one of {', '.join(ROLE_CHOICES)}")
        argv = ["volumes", "assign", mount, "--role", role]
        label = _text_arg(args, "label", required=False)
        return argv + (["--label", label] if label else [])
    if cmd == "ingest":
        _allow_only(args, {"source", "target", "name", "dry_run"})
        argv = ["ingest", "--source", _path_arg(args, "source"), "--target", _path_arg(args, "target")]
        name = _text_arg(args, "name", required=False)
        if name:
            argv += ["--name", name]
        return argv + (["--dry-run"] if _bool_arg(args, "dry_run") else [])
    if cmd == "resume":
        _allow_only(args, {"job_id"})
        job_id = args.get("job_id")
        if isinstance(job_id, bool) or not isinstance(job_id, int) or job_id < 1:
            raise BadRequest("job_id must be a positive integer")
        return ["resume", str(job_id)]
    if cmd in ("card_status", "index", "hash"):
        _allow_only(args, {"mount"})
        word = {"card_status": "card-status", "index": "index", "hash": "hash"}[cmd]
        return [word, _path_arg(args, "mount")]
    if cmd == "meta_scan":
        _allow_only(args, {"mount"})
        return ["meta", "scan", "--volume", _path_arg(args, "mount")]
    if cmd == "meta_refs":
        _allow_only(args, {"folder", "label"})
        return ["meta", "refs", "--folder", _path_arg(args, "folder"), "--label", _text_arg(args, "label")]
    if cmd == "meta_sidecars":
        _allow_only(args, {"mount", "dry_run"})
        argv = ["meta", "sidecars", "--volume", _path_arg(args, "mount")]
        return argv + (["--dry-run"] if _bool_arg(args, "dry_run") else [])
    raise BadRequest(f"unknown command {cmd!r}")


ALLOWED_CMDS = ("assign", "ingest", "resume", "card_status", "index", "hash", "meta_scan", "meta_refs",
                "meta_sidecars")


# --------------------------------------------------------------------------- app and helpers

@dataclass
class WebApp:
    catalog_path: Path
    home: Path
    volumes_root: Path
    runner: TaskRunner


class _NullEmitter:
    """Drive probe warnings are not shown in the web UI; they are dropped here."""

    def emit(self, event: str, **fields: Any) -> None:
        return None


def _drive_list(conn, root: Path) -> list[dict]:
    try:
        mounted = scan_mounts(conn, root, _NullEmitter())
    except OSError:
        mounted = []
    seen = {m["volume_id"] for m in mounted if m["volume_id"]}
    rows = list(mounted)
    for v in repo.list_volumes(conn):
        if v["volume_id"] in seen:
            continue
        rows.append({
            "mount": None, "role": v["role"], "label": v["label"], "fs": v["fs_type"] or "",
            "size": human_bytes(v["capacity_bytes"]) if v["capacity_bytes"] is not None else "?",
            "free": None, "volume_id": v["volume_id"], "mounted": False, "looks_like_card": False,
            "last_seen_at": v["last_seen_at"],
        })
    return sorted(rows, key=lambda r: not r["mounted"])  # mounted first; stable within each group


def _int_param(query: dict, key: str, default: int, lo: int, hi: int) -> int:
    raw = query.get(key, [None])[0]
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise BadRequest(f"{key} must be an integer") from exc
    return min(max(value, lo), hi)


def _opt_param(query: dict, key: str) -> str | None:
    raw = query.get(key, [None])[0]
    return raw if raw else None


def _group_row(row) -> dict:
    d = dict(row)
    d["keywords"] = json.loads(d.pop("keywords_json") or "[]")
    if d.get("dirty") is not None:
        d["dirty"] = bool(d["dirty"])
    return d


def _thumb_source(conn, group_id: int) -> Path | None:
    """The primary file of a group on a drive that is mounted right now, or None."""
    rows = conn.execute(
        """
        SELECT i.rel_path, v.marker_id, v.da_volume_uuid, v.last_mount_path
        FROM assets a
        JOIN instances i ON i.asset_id = a.asset_id AND i.state IN ('indexed', 'verified')
        JOIN volumes v ON v.volume_id = i.volume_id
        WHERE a.group_id = ? AND a.group_role = 'primary'
        ORDER BY (v.role = 'WORKING') DESC, i.instance_id
        """,
        (group_id,),
    ).fetchall()
    for row in rows:
        if not row["last_mount_path"]:
            continue
        mount = Path(row["last_mount_path"])
        if not mount.is_absolute() or not mount.is_dir():
            continue
        if not _is_same_volume(mount, row):
            continue
        base = mount.resolve()
        src = (base / row["rel_path"]).resolve()
        if src.is_relative_to(base) and src.is_file():
            return src
    return None


def _is_same_volume(mount: Path, row) -> bool:
    try:
        if row["marker_id"]:
            return volumes.read_marker(mount) == row["marker_id"]
        if row["da_volume_uuid"]:
            return volumes.probe(mount).da_volume_uuid == row["da_volume_uuid"]
    except (RuntimeError, OSError, ValueError):
        return False
    return False


def _thumb(app: WebApp, conn, group_id: int) -> Raw:
    src = _thumb_source(conn, group_id)
    if src is None:
        raise NotFound("drive not mounted or no file for this group")
    cache = app.home / "thumbs" / f"{group_id}.jpg"
    if cache.is_file():
        return Raw(200, "image/jpeg", cache.read_bytes())
    data = preview.extract_preview(src)
    if data is None:
        raise NotFound("no embedded preview")
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, cache)
    except OSError as exc:
        logger.warning("could not cache thumbnail for group %s: %s", group_id, exc)
    return Raw(200, "image/jpeg", data)


def _settings(conn) -> dict:
    out: dict[str, Any] = {}
    for key, default in SETTING_DEFAULTS.items():
        value = repo.get_setting(conn, key, default)
        out[key] = int(value) if key == "match_window_s" else value
    return out


def _check_meta_fields(fields: Any) -> dict:
    if not isinstance(fields, dict) or not fields:
        raise BadRequest("fields must be a non-empty object")
    for key, value in fields.items():
        if key in ("lat", "lon", "alt"):
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise BadRequest(f"{key} must be a number or null")
        elif key == "time_shift_s":
            if isinstance(value, bool) or not isinstance(value, int):
                raise BadRequest("time_shift_s must be an integer")
        elif key == "gps_source":
            if value is not None and value not in GPS_SOURCES:
                raise BadRequest(f"gps_source must be one of {', '.join(GPS_SOURCES)} or null")
        elif key in ("place_name", "creator", "copyright", "title"):
            if value is not None and not isinstance(value, str):
                raise BadRequest(f"{key} must be a string or null")
        elif key in ("keywords_add", "keywords_remove", "keywords_set"):
            if value is not None and (not isinstance(value, list) or not all(isinstance(w, str) for w in value)):
                raise BadRequest(f"{key} must be a list of strings")
    return fields


def _group_ids(body: dict) -> list[int]:
    ids = body.get("group_ids")
    if not isinstance(ids, list) or not ids or not all(
            isinstance(g, int) and not isinstance(g, bool) and g >= 1 for g in ids):
        raise BadRequest("group_ids must be a non-empty list of positive integers")
    return ids


def _body_dict(body: Any) -> dict:
    if not isinstance(body, dict):
        raise BadRequest("body must be a JSON object")
    return body


# --------------------------------------------------------------------------- routing

def route(app: WebApp, method: str, path: str, query: dict, body: Any) -> tuple[int, Any]:
    """Dispatch one API call. Returns `(status, payload)`; payload is JSON-able or a `Raw`."""
    conn = db.connect(app.catalog_path)
    try:
        return _route(app, conn, method, path, query, body)
    finally:
        conn.close()


def _route(app: WebApp, conn, method: str, path: str, query: dict, body: Any) -> tuple[int, Any]:
    m = re.fullmatch(r"/api/tasks/(\d+)(/pause)?", path)
    if m:
        task_id = int(m.group(1))
        if m.group(2):
            if method != "POST":
                return 405, {"error": "method not allowed"}
            return 200, app.runner.pause(task_id)
        if method != "GET":
            return 405, {"error": "method not allowed"}
        since = _int_param(query, "since", -1, -1, 2**62)
        return 200, app.runner.get(task_id).detail(since)

    m = re.fullmatch(r"/api/groups/(\d+)/(suggestions|thumb)", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        group_id = int(m.group(1))
        if m.group(2) == "thumb":
            return 200, _thumb(app, conn, group_id)
        try:
            window = int(repo.get_setting(conn, "match_window_s", SETTING_DEFAULTS["match_window_s"]))
            found = suggest(conn, group_id, window_s=window)
        except ValueError as exc:
            raise NotFound(str(exc)) from exc
        return 200, {"suggestions": found}

    routes: dict[tuple[str, str], Any] = {
        ("GET", "/api/drives"): lambda: {"drives": _drive_list(conn, app.volumes_root)},
        ("GET", "/api/jobs"): lambda: {"jobs": [dict(r) for r in repo.list_jobs(
            conn, _int_param(query, "limit", 20, 1, 200))]},
        ("POST", "/api/run"): lambda: _run(app, body),
        ("GET", "/api/tasks"): lambda: {"tasks": app.runner.summaries()},
        ("GET", "/api/shoots"): lambda: {"shoots": [dict(r) for r in repo.list_shoots(conn)]},
        ("GET", "/api/groups"): lambda: _groups(conn, query),
        ("POST", "/api/groups/meta"): lambda: _groups_meta(conn, body),
        ("GET", "/api/settings"): lambda: {"settings": _settings(conn)},
        ("POST", "/api/settings"): lambda: _post_settings(conn, body),
    }
    handler = routes.get((method, path))
    if handler is not None:
        return 200, handler()
    if any(p == path for _, p in routes):
        return 405, {"error": "method not allowed"}
    return 404, {"error": "unknown endpoint"}


def _run(app: WebApp, body: Any) -> dict:
    body = _body_dict(body)
    cmd = body.get("cmd")
    if cmd not in ALLOWED_CMDS:
        raise BadRequest(f"unknown command {cmd!r}; allowed: {', '.join(ALLOWED_CMDS)}")
    args = body.get("args", {})
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise BadRequest("args must be an object")
    return {"task_id": app.runner.start(build_argv(cmd, args))}


def _groups(conn, query: dict) -> dict:
    volume_id = _opt_param(query, "volume_id")
    shoot_raw = _opt_param(query, "shoot_id")
    try:
        shoot_id = int(shoot_raw) if shoot_raw is not None else None
    except ValueError as exc:
        raise BadRequest("shoot_id must be an integer") from exc
    missing = _opt_param(query, "missing")
    limit = _int_param(query, "limit", 200, 1, 1000)
    offset = _int_param(query, "offset", 0, 0, 2**62)
    try:
        rows = repo.list_groups(conn, volume_id=volume_id, shoot_id=shoot_id, missing=missing, limit=limit,
                                offset=offset)
        counts = repo.missing_counts(conn, volume_id=volume_id)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return {"groups": [_group_row(r) for r in rows], "missing_counts": counts}


def _groups_meta(conn, body: Any) -> dict:
    body = _body_dict(body)
    ids = _group_ids(body)
    fields = _check_meta_fields(body.get("fields"))
    try:
        with repo.transaction(conn):
            updated = repo.update_group_meta(conn, ids, fields=fields)
    except sqlite3.IntegrityError as exc:  # an unknown group id trips the foreign key
        raise BadRequest("unknown group id") from exc
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return {"updated": updated}


def _post_settings(conn, body: Any) -> dict:
    body = _body_dict(body)
    unknown = set(body) - set(SETTING_DEFAULTS)
    if unknown:
        raise BadRequest(f"unknown setting(s): {', '.join(sorted(unknown))}")
    if not body:
        raise BadRequest("no settings given")
    values: dict[str, str] = {}
    for key, value in body.items():
        if key in ("creator", "copyright"):
            if not isinstance(value, str):
                raise BadRequest(f"{key} must be a string")
            values[key] = value.strip()
        elif key == "default_offset":
            if not isinstance(value, str):
                raise BadRequest("default_offset must be a string such as +01:00")
            try:
                offset_tz(value)
            except ValueError as exc:
                raise BadRequest(str(exc)) from exc
            values[key] = value.strip()
        else:  # match_window_s
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise BadRequest("match_window_s must be a whole number of seconds")
            try:
                seconds = int(value)
            except ValueError as exc:
                raise BadRequest("match_window_s must be a whole number of seconds") from exc
            if not 0 <= seconds <= 86_400:
                raise BadRequest("match_window_s must be between 0 and 86400")
            values[key] = str(seconds)
    with repo.transaction(conn):
        for key, value in values.items():
            repo.set_setting(conn, key, value)
    return {"settings": _settings(conn)}


# --------------------------------------------------------------------------- HTTP

class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: WebApp) -> None:
        super().__init__(address, _Handler)
        self.app = app


class _Handler(BaseHTTPRequestHandler):
    server: _Server  # type: ignore[assignment]

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return None

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def _dispatch(self, method: str) -> None:
        try:
            if not self._host_ok():
                return self._send_json(403, {"error": "host not allowed"})
            url = urlsplit(self.path)
            path = unquote(url.path)
            if method == "GET" and path == "/":
                return self._static("index.html")
            if method == "GET" and path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path.startswith("/api/"):
                body = self._read_body() if method == "POST" else None
                query = parse_qs(url.query, keep_blank_values=True)
                status, payload = route(self.server.app, method, path, query, body)
                if isinstance(payload, Raw):
                    return self._send(status, payload.body, payload.content_type)
                return self._send_json(status, payload)
            return self._send_json(404, {"error": "not found"})
        except BadRequest as exc:
            self._send_json(400, {"error": str(exc)})
        except NotFound as exc:
            self._send_json(404, {"error": str(exc)})
        except Conflict as exc:
            self._send_json(409, {"error": str(exc)})
        except Exception:  # noqa: BLE001 - the API answers 500 instead of dropping the connection
            logger.exception("request failed: %s", self.path)
            self._send_json(500, {"error": "internal error"})

    def _host_ok(self) -> bool:
        port = self.server.server_address[1]
        return self.headers.get("Host") in {fmt.format(port=port) for fmt in ALLOWED_HOSTS_FMT}

    def _read_body(self) -> Any:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise BadRequest("bad Content-Length") from exc
        if length < 0 or length > MAX_BODY:
            raise BadRequest("request body too large or negative")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise BadRequest("body is not valid JSON") from exc

    def _static(self, rel: str) -> None:
        base = STATIC_DIR.resolve()
        target = (base / rel).resolve()
        if not target.is_relative_to(base) or not target.is_file():
            raise NotFound("not found")
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)

    def _send_json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload, default=str).encode("utf-8"), "application/json")

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def make_server(app: WebApp, port: int = 8765) -> _Server:
    """A server bound to 127.0.0.1. Port 0 picks a free port (read it from `server_address`)."""
    return _Server(("127.0.0.1", port), app)
