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
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
from collections import Counter, deque
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from photovault import activity, cards, preview, usage, volumes
from photovault.catalog import db, repo
from photovault.drives import DEFAULT_MAC_FOLDER, VOLUMES_ROOT, scan_mounts
from photovault.geo import offset_tz, suggest
from photovault.protocol import EXIT_PAUSED, human_bytes

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
ROLE_CHOICES = ("WORKING", "MASTER", "LEGACY")
SETTING_DEFAULTS = {"creator": "", "copyright": "", "default_offset": "+00:00", "match_window_s": "1800",
                    "mac_folder": DEFAULT_MAC_FOLDER}
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


def default_awake_argv() -> list[str] | None:
    """`caffeinate` blocking idle, system (on AC) and disk sleep until a pid exits (ui-12); None off macOS."""
    exe = shutil.which("caffeinate") if sys.platform == "darwin" else None
    return [exe, "-ims", "-w"] if exe else None


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
        self.keep_awake = False

    def add(self, event: dict) -> None:
        event = {**event, "seq": self.next_seq}
        self.next_seq += 1
        self.events.append(event)

    def finish(self, state: str, exit_code: int | None) -> None:
        self.state = state
        self.exit_code = exit_code

    def summary(self) -> dict:
        return {"task_id": self.task_id, "state": self.state, "exit_code": self.exit_code, "argv": self.argv,
                "last_seq": self.next_seq - 1, "keep_awake": self.keep_awake}

    def detail(self, since: int) -> dict:
        events = [e for e in self.events if e["seq"] > since]
        progress = [e for e in self.events if e.get("event") == "progress"]
        return {**self.summary(), "events": events, "latest_progress": progress[-1] if progress else None}


class TaskRunner:
    """Runs engine commands as subprocesses and keeps their events in memory (for the life of the server).

    `awake_argv` (ui-12) is started with the task's pid appended, to keep the Mac awake until the task exits.
    By default it is `default_awake_argv()` when running the real engine, and off for a custom `base_argv`.
    """

    def __init__(self, base_argv: list[str] | None = None, awake_argv: list[str] | None | str = "auto") -> None:
        self.base_argv = list(base_argv) if base_argv is not None else default_base_argv()
        if awake_argv == "auto":
            awake_argv = default_awake_argv() if base_argv is None else None
        self.awake_argv = list(awake_argv) if awake_argv else None
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
        if self.awake_argv:
            try:  # it exits on its own when the task does (`-w <pid>`)
                subprocess.Popen([*self.awake_argv, str(proc.pid)], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                task.keep_awake = True
            except OSError as exc:
                logger.warning("cannot keep the Mac awake: %s", exc)
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
    if cmd == "card_check":
        _allow_only(args, {"mount"})
        return ["cards", "check", _path_arg(args, "mount")]
    if cmd == "meta_scan":
        _allow_only(args, {"mount", "rescan"})
        argv = ["meta", "scan", "--volume", _path_arg(args, "mount")]
        return argv + (["--rescan"] if _bool_arg(args, "rescan") else [])
    if cmd == "meta_refs":
        _allow_only(args, {"folder", "label"})
        return ["meta", "refs", "--folder", _path_arg(args, "folder"), "--label", _text_arg(args, "label")]
    if cmd == "meta_sidecars":
        _allow_only(args, {"mount", "dry_run"})
        argv = ["meta", "sidecars", "--volume", _path_arg(args, "mount")]
        return argv + (["--dry-run"] if _bool_arg(args, "dry_run") else [])
    raise BadRequest(f"unknown command {cmd!r}")


ALLOWED_CMDS = ("assign", "ingest", "resume", "card_status", "card_check", "index", "hash", "meta_scan",
                "meta_refs", "meta_sidecars")


# --------------------------------------------------------------------------- app and helpers

SSE_PING_S = 25.0          # a comment line keeps idle event streams open
STAT_POLL_S = 2.0          # fallback check interval when kqueue is unavailable
O_EVTONLY = 0x8000         # macOS: open a path for events only (not exposed by Python's os module)


class VolumeWatcher:
    """Counts changes under the volumes root so the UI can refetch drives at once.

    On macOS a kqueue watch on the root blocks in kevent() until the folder changes; there is no polling.
    A self-pipe wakes that wait so `stop()` returns promptly. Without kqueue, the root's mtime is checked every
    `poll_s` seconds. Each change bumps `generation` and notifies `cond`.
    """

    def __init__(self, root: Path, *, use_kqueue: bool = True, poll_s: float = STAT_POLL_S) -> None:
        self.root = Path(root)
        self.use_kqueue = use_kqueue and hasattr(select, "kqueue")
        self.poll_s = poll_s
        self.cond = threading.Condition()
        self.generation = 0
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._wake_lock = threading.Lock()
        self._wake_r: int | None = None
        self._wake_w: int | None = None
        self._thread: threading.Thread | None = None

    @property
    def stopped(self) -> bool:
        return self._stop.is_set()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="volume-watcher", daemon=True)
        self._thread.start()
        self._ready.wait(2.0)  # changes made after start() returns are seen

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        with self._wake_lock:
            if self._wake_w is not None:
                try:
                    os.write(self._wake_w, b"x")
                except OSError:
                    pass
        with self.cond:
            self.cond.notify_all()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(timeout)

    def wait_change(self, since: int, timeout: float) -> bool:
        """True when the generation moved past `since`. False on timeout or after stop()."""
        with self.cond:
            self.cond.wait_for(lambda: self.generation != since or self._stop.is_set(), timeout)
            return self.generation != since

    def _bump(self) -> None:
        with self.cond:
            self.generation += 1
            self.cond.notify_all()

    def _run(self) -> None:
        if self.use_kqueue and self._run_kqueue():
            return
        self._run_stat()

    def _run_kqueue(self) -> bool:
        """Watch with kqueue until stop(). True when it ran and stopped; False when it could not start."""
        flag = O_EVTONLY if sys.platform == "darwin" else os.O_RDONLY
        try:
            fd = os.open(self.root, flag)
        except OSError:
            return False
        try:
            kq = select.kqueue()
        except OSError:
            os.close(fd)
            return False
        r, w = os.pipe()
        with self._wake_lock:
            self._wake_r, self._wake_w = r, w
        fflags = (select.KQ_NOTE_WRITE | select.KQ_NOTE_DELETE | select.KQ_NOTE_RENAME
                  | select.KQ_NOTE_LINK)
        try:
            kq.control([
                select.kevent(fd, filter=select.KQ_FILTER_VNODE, flags=select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                              fflags=fflags),
                select.kevent(r, filter=select.KQ_FILTER_READ, flags=select.KQ_EV_ADD),
            ], 0, None)
            self._ready.set()
            while not self._stop.is_set():
                for event in kq.control(None, 8, None):  # blocks until the root changes or stop() wakes us
                    if event.ident == r:
                        return True
                    if event.ident == fd:
                        self._bump()
            return True
        except OSError:
            logger.warning("kqueue watch failed; checking the volumes folder every %s s", self.poll_s,
                           exc_info=True)
            return False
        finally:
            with self._wake_lock:
                self._wake_r = self._wake_w = None
            kq.close()
            for handle in (fd, r, w):
                os.close(handle)

    def _mtime(self) -> int | None:
        try:
            return os.stat(self.root).st_mtime_ns
        except OSError:
            return None

    def _run_stat(self) -> None:
        last = self._mtime()
        self._ready.set()
        while not self._stop.wait(self.poll_s):
            now = self._mtime()
            if now != last:
                last = now
                self._bump()


@dataclass
class WebApp:
    catalog_path: Path
    home: Path
    volumes_root: Path
    runner: TaskRunner
    watcher: VolumeWatcher | None = None


class _NullEmitter:
    """Drive probe warnings are not shown in the web UI; they are dropped here."""

    def emit(self, event: str, **fields: Any) -> None:
        return None


IGNORED_DRIVES_KEY = "ignored_drives"  # a JSON list of drive keys the sidebar folds away (no schema change)


def _drive_key(row: dict) -> str:
    """A stable key for ignoring a drive: its volume_id once registered, else the folder name it mounts as."""
    if row.get("volume_id"):
        return row["volume_id"]
    if row.get("local"):
        return "local:"
    return "mount:" + Path(row["mount"]).name


def _ignored_drives(conn) -> list[str]:
    try:
        keys = json.loads(repo.get_setting(conn, IGNORED_DRIVES_KEY, "[]") or "[]")
    except ValueError:
        return []
    return [k for k in keys if isinstance(k, str)] if isinstance(keys, list) else []


def _post_ignore_drive(conn, body: Any) -> dict:
    body = _body_dict(body)
    key, ignored = body.get("key"), body.get("ignored")
    if not isinstance(key, str) or not key.strip() or len(key) > 512:
        raise BadRequest("key must be a drive key from /api/drives")
    if not isinstance(ignored, bool):
        raise BadRequest("ignored must be true or false")
    keys = [k for k in _ignored_drives(conn) if k != key]
    if ignored:
        keys.append(key)
    with repo.transaction(conn):
        repo.set_setting(conn, IGNORED_DRIVES_KEY, json.dumps(keys))
    return {"ignored": keys}


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
            "last_seen_at": v["last_seen_at"], "local": False,
        })
    status = repo.meta_scan_status(conn)
    ignored = set(_ignored_drives(conn))
    for r in rows:
        r["key"] = _drive_key(r)
        r["ignored"] = r["key"] in ignored  # every photo on the drive has its metadata read: the UI offers "Scan Again"
        groups, scanned = status.get(r.get("volume_id") or "", (0, 0))
        r["meta_scanned"] = groups > 0 and scanned >= groups
        r["steps"] = repo.volume_last_steps(conn, r["volume_id"]) if r.get("volume_id") else None
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
    """The primary file of a group on a drive that is mounted right now, or None. Files not hashed yet have no
    asset link, so their index job's record is used instead (``repo.indexed_primary_files``)."""
    rows = [dict(r) for r in conn.execute(
        """
        SELECT i.rel_path, v.marker_id, v.da_volume_uuid, v.last_mount_path
        FROM assets a
        JOIN instances i ON i.asset_id = a.asset_id AND i.state IN ('indexed', 'verified')
        JOIN volumes v ON v.volume_id = i.volume_id
        WHERE a.group_id = ? AND a.group_role = 'primary'
        ORDER BY (v.role = 'WORKING') DESC, i.instance_id
        """,
        (group_id,),
    ).fetchall()]
    if not rows:
        for f in repo.indexed_primary_files(conn, [group_id]).get(group_id, []):
            vol = conn.execute("SELECT marker_id, da_volume_uuid, last_mount_path FROM volumes WHERE volume_id = ?",
                               (f["volume_id"],)).fetchone()
            if vol is not None:
                rows.append({"rel_path": f["rel_path"], **dict(vol)})
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

    m = re.fullmatch(r"/api/volumes/([^/]+)(?:/(files|reveal))?", path)
    if m:
        return _volume_route(app, conn, method, m.group(1), m.group(2), query, body)

    if path == "/api/cards":
        if method != "GET":
            return 405, {"error": "method not allowed"}
        return 200, cards.list_cards(conn, _drive_list(conn, app.volumes_root))

    m = re.fullmatch(r"/api/cards/([^/]+)/files", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        return 200, _card_files(app, conn, m.group(1), query)

    m = re.fullmatch(r"/api/assets/(\d+)/copies", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        return 200, _asset_copies(app, conn, int(m.group(1)))

    routes: dict[tuple[str, str], Any] = {
        ("GET", "/api/drives"): lambda: {"drives": _drive_list(conn, app.volumes_root)},
        ("POST", "/api/drives/ignore"): lambda: _post_ignore_drive(conn, body),
        ("GET", "/api/jobs"): lambda: {"jobs": [dict(r) for r in repo.list_jobs(
            conn, _int_param(query, "limit", 20, 1, 200))]},
        ("POST", "/api/run"): lambda: _run(app, body),
        ("GET", "/api/tasks"): lambda: {"tasks": app.runner.summaries()},
        ("GET", "/api/activity"): lambda: activity.snapshot(conn, app.home, server_pid=os.getpid()),
        ("GET", "/api/shoots"): lambda: {"shoots": [dict(r) for r in repo.list_shoots(conn)]},
        ("GET", "/api/groups"): lambda: _groups(conn, query),
        ("GET", "/api/photo-days"): lambda: _photo_days(conn, query),
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


FILE_STATES = ("indexed", "verified", "mismatch", "missing")
RUN_LABELS = {"index_volume": "Index", "baseline_hash": "Hash"}


def _volume_route(app: WebApp, conn, method: str, volume_id: str, sub: str | None, query: dict,
                  body: Any) -> tuple[int, Any]:
    if sub == "reveal":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return 200, _reveal(app, conn, volume_id, _body_dict(body))
    if method != "GET":
        return 405, {"error": "method not allowed"}
    if repo.get_volume(conn, volume_id) is None:
        raise NotFound("unknown drive")
    if sub == "files":
        state = _opt_param(query, "state")
        if state is not None and state not in FILE_STATES:
            raise BadRequest(f"state must be one of: {', '.join(FILE_STATES)}")
        copies = _opt_param(query, "copies")
        if copies is not None and copies not in repo.COPY_FILTERS:
            raise BadRequest(f"copies must be one of: {', '.join(repo.COPY_FILTERS)}")
        other = _opt_param(query, "other")
        if other is not None and (other == volume_id or repo.get_volume(conn, other) is None):
            raise BadRequest("other must be another known drive")
        camera = _camera_param(query)
        limit = _int_param(query, "limit", 200, 1, 1000)
        offset = _int_param(query, "offset", 0, 0, 2**62)
        filters = {"state": state, "copies": copies, "other_volume_id": other, "camera": camera}
        rows = repo.volume_files(conn, volume_id, limit=limit, offset=offset, **filters)
        names = cards.camera_names(conn)
        return 200, {"files": [_file_row(r, names) for r in rows], "limit": limit, "offset": offset,
                     "total": repo.volume_files_count(conn, volume_id, **filters)}
    return 200, _volume_summary(app, conn, volume_id)


def _card_files(app: WebApp, conn, volume_id: str, query: dict) -> dict:
    """One card's files, most urgent first, filtered by tier and camera. A plugged-in card is walked (no reads)."""
    vol = repo.get_volume(conn, volume_id)
    if vol is None or vol["role"] != "CARD":
        raise NotFound("unknown card")
    tier = _opt_param(query, "tier")
    if tier is not None and tier not in cards.TIERS:
        raise BadRequest(f"tier must be one of: {', '.join(cards.TIERS)}")
    camera = _opt_param(query, "camera")
    copies = _opt_param(query, "copies")
    if copies is not None and copies not in repo.COPY_FILTERS:
        raise BadRequest(f"copies must be one of: {', '.join(repo.COPY_FILTERS)}")
    other = _opt_param(query, "other")
    if other is not None and (other == volume_id or repo.get_volume(conn, other) is None):
        raise BadRequest("other must be another known drive")
    limit = _int_param(query, "limit", 200, 1, 1000)
    offset = _int_param(query, "offset", 0, 0, 2**62)
    drive = next((d for d in _drive_list(conn, app.volumes_root) if d["volume_id"] == volume_id), None)
    mount = Path(drive["mount"]) if drive and drive["mounted"] and drive["mount"] else None
    files = cards.card_files(conn, volume_id, mount=mount)
    names = cards.camera_names(conn)
    summary = cards.card_summary(conn, volume_id, files=files)
    on_card = Counter(f["asset_id"] for f in files if f["asset_id"] is not None)
    for f in files:
        f["camera"] = str(f["body_id"]) if f["body_id"] is not None else cards.UNKNOWN_CAMERA
        f["camera_name"] = names.get(f["body_id"], "Unknown camera") if f["body_id"] is not None else "Unknown camera"
        f["dup_here"] = on_card[f["asset_id"]] - 1 if f["asset_id"] is not None else 0
    if tier is not None:
        files = [f for f in files if f["tier"] == tier]
    if camera is not None:
        files = [f for f in files if f["camera"] == camera]
    if copies == "elsewhere":
        files = [f for f in files if f["copies"]]
    elif copies == "only_here":
        files = [f for f in files if not f["copies"] and f["tier"] != "unchecked"]
    elif copies == "dup_here":
        files = [f for f in files if f["dup_here"] > 0]
    if other is not None:
        files = [f for f in files if any(c["volume_id"] == other for c in f["copies"])]
    return {"card": {"volume_id": volume_id, "label": vol["label"], "mounted": mount is not None,
                     "mount": str(mount) if mount else None, "last_seen_at": vol["last_seen_at"], **summary},
            "files": files[offset:offset + limit], "total": len(files), "limit": limit, "offset": offset}


def _asset_copies(app: WebApp, conn, asset_id: int) -> dict:
    """Every verified copy of one file across drives, with whether each drive is plugged in (for Show in Finder)."""
    asset = conn.execute("SELECT asset_id, size, kind FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()
    if asset is None:
        raise NotFound("unknown file")
    mounted = {d["volume_id"]: d["mounted"] for d in _drive_list(conn, app.volumes_root) if d["volume_id"]}
    copies = [{**dict(r), "lr_referenced": bool(r["lr_referenced"]), "mounted": mounted.get(r["volume_id"], False)}
              for r in repo.asset_locations(conn, asset_id)]
    return {"asset_id": asset_id, "size": asset["size"], "kind": asset["kind"], "copies": copies}


def _camera_param(query: dict) -> str | None:
    """A camera filter: a body_id, or ``unknown`` for files with no known camera."""
    camera = _opt_param(query, "camera")
    if camera is not None and camera != repo.UNKNOWN_CAMERA and not camera.isdigit():
        raise BadRequest(f"camera must be a camera id or {repo.UNKNOWN_CAMERA}")
    return camera


def _file_row(row, names: dict[int, str]) -> dict:
    d = dict(row)
    bid = d.pop("body_id")
    d["camera"] = str(bid) if bid is not None else repo.UNKNOWN_CAMERA
    d["camera_name"] = names.get(bid, "Camera") if bid is not None else "Unknown camera"
    names_raw = d.pop("copy_names") or ""
    d["copy_drives"] = [dict(zip(("label", "role"), x.split("\x1d", 1))) for x in names_raw.split("\x1f") if x]
    d["hashed"] = bool(d["hashed"])
    if not d["hashed"]:
        d["copies"] = d["dup_here"] = None  # unknown until the file is hashed
    d.pop("verified_sha256", None)
    return d


def _drive_row(app: WebApp, conn, volume_id: str) -> dict | None:
    return next((d for d in _drive_list(conn, app.volumes_root) if d["volume_id"] == volume_id), None)


def _volume_summary(app: WebApp, conn, volume_id: str) -> dict:
    drives = _drive_list(conn, app.volumes_root)
    states = {s: {"files": 0, "bytes": 0, "hashed": 0} for s in FILE_STATES}
    for r in repo.volume_state_counts(conn, volume_id):
        states[r["state"]] = {"files": r["files"], "bytes": r["bytes"], "hashed": r["hashed"] or 0}
    kinds = [{"kind": r["kind"], "files": r["files"], "bytes": r["bytes"]}
             for r in repo.volume_kind_counts(conn, volume_id)]
    runs = []
    for r in repo.volume_runs(conn, volume_id):
        params = json.loads(r["params_json"] or "{}")
        runs.append({"job_id": r["job_id"], "kind": r["kind"], "label": RUN_LABELS[r["kind"]], "state": r["state"],
                     "started_at": r["created_at"], "finished_at": r["finished_at"], "result": params.get("result")})
    return {
        "drive": next((d for d in drives if d["volume_id"] == volume_id), None),
        "states": states,
        "files": sum(s["files"] for s in states.values()),
        "bytes": sum(s["bytes"] for s in states.values()),
        "kinds": kinds,
        "runs": runs,
        "steps": repo.volume_last_steps(conn, volume_id),
        "copies": _copy_summary(conn, volume_id, drives),
        "cameras": _volume_cameras(conn, volume_id),
        "usage": _volume_usage(conn, volume_id, states),
    }


def _volume_usage(conn, volume_id: str, states: dict) -> dict | None:
    """Photography vs everything else on the drive (idx-13); None until an index has counted it."""
    tracked = [v for k, v in states.items() if k != "missing"]
    cap = conn.execute("SELECT capacity_bytes FROM volumes WHERE volume_id = ?", (volume_id,)).fetchone()
    return usage.summarize(sum(v["bytes"] for v in tracked), sum(v["files"] for v in tracked),
                           repo.volume_usage(conn, volume_id), cap["capacity_bytes"] if cap else None)


def _volume_cameras(conn, volume_id: str) -> list[dict]:
    """Files per camera on a drive, for the Camera filter; files with no known camera come last."""
    names = cards.camera_names(conn)
    out = [{"camera": str(r["body_id"]) if r["body_id"] is not None else repo.UNKNOWN_CAMERA,
            "name": names.get(r["body_id"], "Camera") if r["body_id"] is not None else "Unknown camera",
            "files": r["files"], "bytes": r["bytes"]}
           for r in repo.volume_camera_counts(conn, volume_id)]
    out.sort(key=lambda c: (c["camera"] == repo.UNKNOWN_CAMERA, -c["files"], c["name"]))
    return out


def _copy_summary(conn, volume_id: str, drives: list[dict]) -> dict:
    mounted = {d["volume_id"]: d["mounted"] for d in drives if d["volume_id"]}
    out = repo.volume_copy_summary(conn, volume_id)
    for v in out["by_volume"]:
        v["mounted"] = mounted.get(v["volume_id"], False)
    return out


def _reveal(app: WebApp, conn, volume_id: str, body: dict) -> dict:
    """Show the drive, or one file on it, in Finder. Only paths inside the drive's current mount are allowed."""
    if sys.platform != "darwin":
        raise BadRequest("Show in Finder works on macOS only")
    drive = _drive_row(app, conn, volume_id)
    if drive is None:
        raise NotFound("unknown drive")
    if not drive["mounted"] or not drive["mount"]:
        raise Conflict("connect this drive to show it in Finder")
    mount = Path(drive["mount"]).resolve()
    rel = body.get("rel_path") or ""
    if not isinstance(rel, str):
        raise BadRequest("rel_path must be text")
    if rel:
        target = (mount / rel).resolve()
        if target == mount or not target.is_relative_to(mount):
            raise BadRequest("that path is not on this drive")
        if not target.exists():
            raise Conflict("that file is not on the drive now")
        argv = ["open", "-R", str(target)]  # -R selects the file in its Finder window
    else:
        target = mount
        argv = ["open", str(target)]
    result = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0:
        raise Conflict(result.stderr.strip() or "Finder did not open")
    return {"opened": str(target)}


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
    day = _opt_param(query, "day")
    limit = _int_param(query, "limit", 200, 1, 1000)
    offset = _int_param(query, "offset", 0, 0, 2**62)
    try:
        rows = repo.list_groups(conn, volume_id=volume_id, shoot_id=shoot_id, missing=missing, day=day, limit=limit,
                                offset=offset)
        counts = repo.missing_counts(conn, volume_id=volume_id)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    groups = [_group_row(r) for r in rows]
    unhashed = [g["group_id"] for g in groups if g["rel_path"] is None]
    if unhashed:  # files indexed but not hashed yet: located through their index job (rv-7)
        found = repo.indexed_primary_files(conn, unhashed)
        for g in groups:
            loc = (found.get(g["group_id"]) or [None])[0]
            if loc and g["rel_path"] is None:
                if volume_id is not None:
                    loc = next((f for f in found[g["group_id"]] if f["volume_id"] == volume_id), loc)
                g["rel_path"], g["volume_id"] = loc["rel_path"], loc["volume_id"]
    return {"groups": groups, "missing_counts": counts}


def _photo_days(conn, query: dict) -> dict:
    """The Photos timeline (rv-7): days with photos, newest first, and the counts of what is missing."""
    volume_id = _opt_param(query, "volume_id")
    missing = _opt_param(query, "missing")
    try:
        days = repo.list_photo_days(conn, volume_id=volume_id, missing=missing)
        counts = repo.missing_counts(conn, volume_id=volume_id)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return {"days": days, "missing_counts": counts}


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
        elif key == "mac_folder":
            if not isinstance(value, str) or not value.strip():
                raise BadRequest("mac_folder must be a folder path such as ~/Sandbox")
            if not volumes.is_folder_volume(Path(value.strip()).expanduser()):
                raise BadRequest("mac_folder must be an existing folder inside your home folder (not home itself)")
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
        self.closing = threading.Event()  # set before the watcher stops, so event streams end

    def server_close(self) -> None:
        self.closing.set()
        if self.app.watcher is not None:
            self.app.watcher.stop()
        super().server_close()


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
            if method == "POST" and not self._post_ok():
                return self._send_json(403, {"error": "POST needs application/json and a local Origin"})
            url = urlsplit(self.path)
            path = unquote(url.path)
            if method == "GET" and path == "/api/events":
                return self._events()
            if method == "GET" and path in ("/", "/next", "/next/"):  # the UI (D-059), a Vite build of web/
                return self._static("next/index.html")
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

    def _post_ok(self) -> bool:
        """Cross-site form and text POSTs cannot send `application/json` without a preflight; an Origin from
        another site is refused too (an absent Origin is a same-origin or non-browser client)."""
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            return False
        origin = self.headers.get("Origin")
        if origin is None:
            return True
        port = self.server.server_address[1]
        return origin in {f"http://{fmt.format(port=port)}" for fmt in ALLOWED_HOSTS_FMT}

    def _events(self) -> None:
        """Server-Sent Events: `event: drives` once at connect and again on each change; a comment every 25 s.
        The stream ends when the client goes away or the server closes."""
        srv = self.server
        watcher = srv.app.watcher
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        try:
            with watcher.cond:
                gen = watcher.generation
            self._sse(f"event: drives\ndata: {json.dumps({'generation': gen})}\n\n")
            while not srv.closing.is_set() and not watcher.stopped:
                if watcher.wait_change(gen, SSE_PING_S):
                    with watcher.cond:
                        gen = watcher.generation
                    self._sse(f"event: drives\ndata: {json.dumps({'generation': gen})}\n\n")
                else:
                    self._sse(": ping\n\n")
        except OSError:  # BrokenPipeError and ConnectionResetError are OSErrors: the client left
            return

    def _sse(self, text: str) -> None:
        self.wfile.write(text.encode("utf-8"))
        self.wfile.flush()

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
    """A server bound to 127.0.0.1. Port 0 picks a free port (read it from `server_address`).
    The drive watcher starts with the server and stops in `server_close()`."""
    httpd = _Server(("127.0.0.1", port), app)
    if app.watcher is None:
        app.watcher = VolumeWatcher(app.volumes_root)
    app.watcher.start()
    return httpd
