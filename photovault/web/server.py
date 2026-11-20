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
import io
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
from collections import Counter, deque
from contextlib import nullcontext as _nullcontext
import time
from datetime import UTC, datetime, timedelta
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

from PIL import Image

from photovault import (outline, backup, targets, actions, activity, cards, clock, metadata, naming, preview, previews, shoots, timemodel, usage,
                        volumes)
from photovault.card_clear import cleared_at
from photovault.catalog import db, repo
from photovault.config import ConfigError, load_config
from photovault.drives import DEFAULT_MAC_FOLDER, VOLUMES_ROOT, scan_mounts
from photovault.geo import group_utc, offset_tz, places_near, suggest, utc_text
from photovault.phone import SOURCES as PHONE_SOURCES
from photovault.protocol import EXIT_PAUSED, human_bytes

logger = logging.getLogger(__name__)
PUMP_POLL_S = 0.2  # how often a task's log file is checked for new lines

STATIC_DIR = Path(__file__).resolve().parent / "static"
ROLE_CHOICES = ("WORKING", "MASTER", "LEGACY")
SETTING_DEFAULTS = {"creator": "", "copyright": "", "default_offset": "+00:00", "match_window_s": "1800",
                    "match_reach_s": "43200",
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
    headers: tuple[tuple[str, str], ...] = ()


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
        # The first job id a result reports (an import's plan), kept apart from `events`: on a long run that event
        # leaves the last-EVENTS_KEPT window, and the UI still needs it to nest a `--follow` copy under its import.
        self.job_id: int | None = None
        # The newest "copying N file(s) from A to B" of a make-green run (rv-36): it names the drive pair, which the
        # command words do not, and copy progress overwrites it within seconds.
        self.route: str | None = None

    def add(self, event: dict) -> None:
        event = {**event, "seq": self.next_seq}
        self.next_seq += 1
        self.events.append(event)
        job_id = event.get("job_id")
        if self.job_id is None and event.get("event") == "result" and isinstance(job_id, int) \
                and not isinstance(job_id, bool):
            self.job_id = job_id
        if event.get("event") == "progress" and event.get("phase") == "make_green" and isinstance(event.get("message"), str):
            self.route = event["message"]

    def finish(self, state: str, exit_code: int | None) -> None:
        self.state = state
        self.exit_code = exit_code

    def summary(self) -> dict:
        return {"task_id": self.task_id, "state": self.state, "exit_code": self.exit_code, "argv": self.argv,
                "last_seq": self.next_seq - 1, "keep_awake": self.keep_awake, "job_id": self.job_id,
                "route": self.route}

    def detail(self, since: int) -> dict:
        events = [e for e in self.events if e["seq"] > since]
        progress = [e for e in self.events if e.get("event") == "progress"]
        return {**self.summary(), "events": events, "latest_progress": progress[-1] if progress else None}


class TaskRunner:
    """Runs engine commands as subprocesses and keeps their events in memory (for the life of the server).

    `awake_argv` (ui-12) is started with the task's pid appended, to keep the Mac awake until the task exits.
    By default it is `default_awake_argv()` when running the real engine, and off for a custom `base_argv`.
    """

    def __init__(self, base_argv: list[str] | None = None, awake_argv: list[str] | None | str = "auto",
                 log_dir: Path | None = None) -> None:
        self.log_dir = Path(log_dir) if log_dir is not None else Path(tempfile.gettempdir()) / "photovault-tasks"
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
        # Detached (headless): the task gets its own session, so Ctrl-C or a closed terminal on `serve` never reaches
        # it, and it writes to a log file rather than a pipe, so it never dies of a broken pipe when `serve` exits. The
        # server follows the file. A run that outlives the server shows on Tasks through its activity file (ui-11).
        log_path = self.log_dir / f"task-{os.getpid()}-{task_id}.log"
        try:
            self.log_dir.mkdir(parents=True, exist_ok=True)
            with open(log_path, "wb") as log:
                proc = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                        start_new_session=True)
        except OSError as exc:
            task.add({"event": "error", "code": "spawn", "message": str(exc)})
            task.finish("failed", None)
            return task_id
        with self._lock:
            self._procs[task_id] = proc
        if self.awake_argv:
            try:  # it exits on its own when the task does (`-w <pid>`)
                subprocess.Popen([*self.awake_argv, str(proc.pid)], stdin=subprocess.DEVNULL,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
                task.keep_awake = True
            except OSError as exc:
                logger.warning("cannot keep the Mac awake: %s", exc)
        threading.Thread(target=self._pump, args=(task, proc, log_path), daemon=True, name=f"task-{task_id}").start()
        return task_id

    def _pump(self, task: _Task, proc: subprocess.Popen, log_path: Path) -> None:
        """Follow the task's log file until the process exits, then read what is left and remove the file."""
        try:
            with open(log_path, encoding="utf-8", errors="replace") as log:
                pending = ""
                while True:
                    chunk = log.read()
                    if chunk:
                        pending += chunk
                        *lines, pending = pending.split("\n")
                        for line in lines:
                            if line.strip():
                                task.add(parse_line(line))
                    elif proc.poll() is not None:
                        if pending.strip():
                            task.add(parse_line(pending))
                        break
                    else:
                        time.sleep(PUMP_POLL_S)
        except OSError as exc:
            task.add({"event": "error", "code": "log", "message": f"lost the task's output: {exc}"})
        finally:
            try:
                log_path.unlink()
            except OSError:
                pass
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

    def running(self) -> int:
        """Tasks of this server still running (for Sleep when done)."""
        with self._lock:
            return sum(1 for p in self._procs.values() if p.poll() is None)

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
        _allow_only(args, {"mount", "role", "label", "change_role"})
        mount = _path_arg(args, "mount")
        role = _text_arg(args, "role")
        if role not in ROLE_CHOICES:
            raise BadRequest(f"role must be one of {', '.join(ROLE_CHOICES)}")
        argv = ["volumes", "assign", mount, "--role", role]
        if _bool_arg(args, "change_role"):
            argv.append("--change-role")
        label = _text_arg(args, "label", required=False)
        return argv + (["--label", label] if label else [])
    if cmd == "ingest":
        _allow_only(args, {"source", "target", "name", "dry_run", "limit"})
        argv = ["ingest", "--source", _path_arg(args, "source"), "--target", _path_arg(args, "target")]
        name = _text_arg(args, "name", required=False)
        if name:
            argv += ["--name", name]
        limit = args.get("limit")
        if limit is not None:
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise BadRequest("limit must be a positive integer")
            argv += ["--limit", str(limit)]
        return argv + (["--dry-run"] if _bool_arg(args, "dry_run") else [])
    if cmd == "backup":
        _allow_only(args, {"card", "source", "target", "follow", "from_folder", "dry_run", "make_green"})
        if _bool_arg(args, "make_green"):
            if any(args.get(k) for k in ("source", "target", "follow", "from_folder")):
                raise BadRequest("'make_green' takes only card (and dry_run)")
            return ["backup", "--card", _text_arg(args, "card"), "--make-green"] + (
                ["--dry-run"] if _bool_arg(args, "dry_run") else [])
        argv = ["backup", "--to", _path_arg(args, "target")]
        if args.get("from_folder"):
            if args.get("card") or args.get("source") or args.get("follow") is not None:
                raise BadRequest("'from_folder' cannot be combined with card, source or follow")
            argv += ["--from-folder", _path_arg(args, "from_folder")]
        else:
            argv += ["--card", _text_arg(args, "card"), "--from", _path_arg(args, "source")]
            follow = args.get("follow")
            if follow is not None:
                if isinstance(follow, bool) or not isinstance(follow, int) or follow < 1:
                    raise BadRequest("follow must be a positive integer")
                argv += ["--follow", str(follow)]
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
    if cmd == "card_clear":
        _allow_only(args, {"mount", "dry_run", "confirm", "verify_all"})
        argv = ["cards", "clear", _path_arg(args, "mount")]
        if "verify_all" in args and not _bool_arg(args, "verify_all"):
            argv.append("--no-verify-all-copies")
        if _bool_arg(args, "dry_run"):
            return argv + ["--dry-run"]
        confirm = args.get("confirm")
        if isinstance(confirm, bool) or not isinstance(confirm, int) or confirm < 1:
            raise BadRequest("confirm must be the planned file count")
        return argv + ["--confirm", str(confirm)]
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
    if cmd == "shoots_plan":
        _allow_only(args, {"from", "to", "gap_h"})
        argv = ["shoots", "plan"]
        for key, flag in (("from", "--from"), ("to", "--to")):
            day = _text_arg(args, key, required=False)
            if day is not None:
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
                    raise BadRequest(f"{key!r} must be YYYY-MM-DD")
                argv += [flag, day]
        gap = args.get("gap_h")
        if gap is not None:
            if isinstance(gap, bool) or not isinstance(gap, (int, float)) or not 0 < gap <= 240:
                raise BadRequest("'gap_h' must be a number of hours above 0")
            argv += ["--gap-h", str(gap)]
        return argv
    if cmd == "previews_cache":
        _allow_only(args, {"mount"})
        return ["previews", "cache", _path_arg(args, "mount")]
    if cmd == "shoots_apply":
        _allow_only(args, {"plan_id", "expect"})
        plan_id = _text_arg(args, "plan_id")
        if not re.fullmatch(r"shoots-\d{8}-\d{6}", plan_id):
            raise BadRequest("'plan_id' must look like shoots-20261118-210000")
        argv = ["shoots", "apply", "--plan", plan_id]
        expect = args.get("expect")
        if expect is not None:
            if isinstance(expect, bool) or not isinstance(expect, int) or expect < 1:
                raise BadRequest("'expect' must be a positive whole number")
            argv += ["--expect", str(expect)]
        return argv
    if cmd == "shoots_undo":
        _allow_only(args, {"apply_id"})
        apply_id = _text_arg(args, "apply_id")
        if not re.fullmatch(r"apply-\d{8}-\d{6}(-\d+)?", apply_id):
            raise BadRequest("'apply_id' must look like apply-20261118-210500")
        return ["shoots", "undo", apply_id]
    raise BadRequest(f"unknown command {cmd!r}")


ALLOWED_CMDS = ("assign", "ingest", "backup", "card_clear", "resume", "card_status", "card_check", "index", "hash", "meta_scan",
                "meta_refs", "meta_sidecars", "shoots_plan", "shoots_apply", "shoots_undo",
                "previews_cache")


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


REPLAN_DELAY_S = 8.0  # after the last photo edit, re-plan shoots once (D-147)


@dataclass
class WebApp:
    catalog_path: Path
    home: Path
    volumes_root: Path
    runner: TaskRunner
    watcher: VolumeWatcher | None = None
    auto_replan: bool = True
    _replan_timer: threading.Timer | None = None
    sleeper: Any = None  # SleepWhenDone (ui-17), made on first use

    def sleep_when_done(self) -> Any:
        if self.sleeper is None:
            from photovault.web.sleep import SleepWhenDone
            self.sleeper = SleepWhenDone(self.runner.running)
        return self.sleeper

    def schedule_replan(self) -> None:
        """Re-plan shoots a few seconds after the last edit (debounced), so names and suggestions follow what the user
        saved. Runs as an ordinary task; the Photos and Review views reload when it finishes."""
        if not self.auto_replan:
            return
        if self._replan_timer is not None:
            self._replan_timer.cancel()

        def fire() -> None:
            self._replan_timer = None
            try:
                self.runner.start(build_argv("shoots_plan", {}))
            except Exception as exc:  # noqa: BLE001  (a failed re-plan must never break a save)
                logger.warning("automatic re-plan did not start: %s", exc)

        self._replan_timer = threading.Timer(REPLAN_DELAY_S, fire)
        self._replan_timer.daemon = True
        self._replan_timer.start()


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
    """The primary file of a group on a drive that is mounted right now, or None."""
    found = _thumb_source_from(conn, group_id)
    return found[0] if found else None


def _thumb_source_from(conn, group_id: int) -> tuple[Path, str] | None:
    """``(file, volume_id)`` of the primary file of a group on a drive that is mounted right now, or None. Files not
    hashed yet have no asset link, so their index job's record is used instead (``repo.indexed_primary_files``)."""
    rows = [dict(r) for r in conn.execute(
        """
        SELECT i.rel_path, v.volume_id, v.marker_id, v.da_volume_uuid, v.last_mount_path
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
            vol = conn.execute("SELECT volume_id, marker_id, da_volume_uuid, last_mount_path FROM volumes "
                               "WHERE volume_id = ?", (f["volume_id"],)).fetchone()
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
            return src, row["volume_id"]
    return None


def _group_detail(app: WebApp, conn, group_id: int) -> dict:
    """What the inspector header shows (rv-21): every drive holding the photo, which one the preview is read from,
    and the exposure from ExifTool (read-only, cached under ``<home>/exif``; null when no drive is mounted)."""
    if conn.execute("SELECT 1 FROM capture_groups WHERE group_id = ?", (group_id,)).fetchone() is None:
        raise NotFound("unknown photo")
    rows = [dict(r) for r in conn.execute(
        """
        SELECT i.volume_id, v.label, v.role, i.rel_path, i.state
        FROM assets a
        JOIN instances i ON i.asset_id = a.asset_id AND i.state IN ('indexed', 'verified')
        JOIN volumes v ON v.volume_id = i.volume_id
        WHERE a.group_id = ? AND a.group_role = 'primary'
        ORDER BY (i.state = 'verified') DESC, v.label, i.rel_path
        """, (group_id,)).fetchall()]
    if not rows:
        for f in repo.indexed_primary_files(conn, [group_id]).get(group_id, []):
            vol = conn.execute("SELECT label, role FROM volumes WHERE volume_id = ?", (f["volume_id"],)).fetchone()
            if vol is not None:
                rows.append({"volume_id": f["volume_id"], "label": vol["label"], "role": vol["role"],
                             "rel_path": f["rel_path"], "state": "indexed"})
    mounted = {d["volume_id"]: d["mounted"] for d in _drive_list(conn, app.volumes_root) if d["volume_id"]}
    instances = [{**r, "mounted": bool(mounted.get(r["volume_id"], False))} for r in rows]
    found = _thumb_source_from(conn, group_id)
    return {"group_id": group_id, "instances": instances,
            "preview_from": found[1] if found else None,
            "exif": _exif(app, group_id, found[0] if found else None)}


def _exif(app: WebApp, group_id: int, src: Path | None) -> dict | None:
    cache = app.home / "exif" / f"{group_id}.json"
    try:
        if cache.is_file():
            return json.loads(cache.read_text())
    except (OSError, ValueError):
        pass
    if src is None:
        return None
    meta = metadata.read_metadata([src], workers=1).meta.get(src) or {}
    out = {"exposure_s": meta.get("ExposureTime"), "fnumber": meta.get("FNumber"), "iso": meta.get("ISO"),
           "focal_mm": meta.get("FocalLength"), "lens": meta.get("LensModel")}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache.with_name(cache.name + ".tmp")
        tmp.write_text(json.dumps(out))
        os.replace(tmp, cache)
    except OSError as exc:
        logger.warning("could not cache exif for group %s: %s", group_id, exc)
    return out


def _is_same_volume(mount: Path, row) -> bool:
    try:
        if row["marker_id"]:
            return volumes.read_marker(mount) == row["marker_id"]
        if row["da_volume_uuid"]:
            return volumes.probe(mount).da_volume_uuid == row["da_volume_uuid"]
    except (RuntimeError, OSError, ValueError):
        return False
    return False


def _thumb(app: WebApp, conn, group_id: int, *, large: bool = False) -> Raw:
    """The embedded JPEG: the small thumbnail, or with ``large`` the biggest one for the viewer (rv-20). A cached copy
    is served first, so it shows while the drive is unplugged (act-4)."""
    cached = previews.read_cached(app.home, group_id, large=large)
    if cached is not None:
        return Raw(200, "image/jpeg", cached)
    if not large:
        folder = previews.review_folder_if_enabled(conn)
        review = previews.read_review(folder, group_id) if folder is not None else None
        if review is not None:
            return Raw(200, "image/jpeg", review)
    src = _thumb_source(conn, group_id)
    if src is None:
        raise NotFound("drive not mounted or no file for this group")
    data = (preview.extract_preview(src, tags=preview.LARGE_PREVIEW_TAGS) if large
            else preview.extract_preview(src))
    if data is None:
        raise NotFound("no embedded preview")
    try:
        previews.store(app.home, group_id, data, large=large)
    except OSError as exc:
        logger.warning("could not cache thumbnail for group %s: %s", group_id, exc)
    return Raw(200, "image/jpeg", data)


PLAIN_IMAGE_EXTS = {".jpg", ".jpeg", ".png"}


def _needs(conn, group_id: int) -> list[dict]:
    """The drives holding the photo's primary file, WORKING first, then the most recently seen (rv-23)."""
    rows = conn.execute(
        """SELECT DISTINCT v.volume_id, v.label, v.role, v.last_seen_at FROM assets a
           JOIN instances i ON i.asset_id = a.asset_id AND i.state IN ('indexed', 'verified')
           JOIN volumes v ON v.volume_id = i.volume_id
           WHERE a.group_id = ? AND a.group_role = 'primary'""", (group_id,)).fetchall()
    found = {r["volume_id"]: dict(r) for r in rows}
    if not found:
        for f in repo.indexed_primary_files(conn, [group_id]).get(group_id, []):
            vol = conn.execute("SELECT volume_id, label, role, last_seen_at FROM volumes WHERE volume_id = ?",
                               (f["volume_id"],)).fetchone()
            if vol is not None:
                found[vol["volume_id"]] = dict(vol)
    ordered = sorted(found.values(), key=lambda r: r["last_seen_at"] or "", reverse=True)
    ordered.sort(key=lambda r: r["role"] != "WORKING")
    return [{"volume_id": r["volume_id"], "label": r["label"], "role": r["role"]} for r in ordered]


def _image_size(data: bytes | Path) -> tuple[int, int] | None:
    try:
        with Image.open(data if isinstance(data, Path) else io.BytesIO(data)) as im:  # header only
            return im.size
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def _full_reply(data: bytes, ctype: str, full: bool, size: tuple[int, int] | None) -> tuple[int, Raw]:
    headers = [("X-Image-Full", "1" if full else "0"), ("Access-Control-Expose-Headers", "X-Image-Width, X-Image-Height, X-Image-Full")]
    if size:
        headers += [("X-Image-Width", str(size[0])), ("X-Image-Height", str(size[1]))]
    return 200, Raw(200, ctype, data, tuple(headers))


def _full_preview(app: WebApp, conn, group_id: int) -> tuple[int, Any]:
    """``GET /api/groups/<id>/preview?full=1`` (rv-23, D-077): the biggest picture the file has. A JPEG/PNG primary is
    the file itself (opened "rb"); a raw gives its ``JpgFromRaw`` (full resolution, ``X-Image-Full: 1``), else
    ``PreviewImage`` (0); TIFF/PSD their embedded preview (0). Sizes go in ``X-Image-Width/Height``. Embedded JPEGs
    are cached under ``<home>/previews/full/`` (a cached one serves with the drive away); no drive mounted and
    nothing cached is a 409 listing the drives to plug in."""
    if conn.execute("SELECT 1 FROM capture_groups WHERE group_id = ?", (group_id,)).fetchone() is None:
        raise NotFound("unknown photo")
    cdir = app.home / "previews" / "full"
    for flag, name in ((True, f"{group_id}.full.jpg"), (False, f"{group_id}.part.jpg")):
        cached = cdir / name
        if cached.is_file():
            data = cached.read_bytes()
            return _full_reply(data, "image/jpeg", flag, _image_size(data))
    src = _thumb_source(conn, group_id)
    if src is None:
        needs = _needs(conn, group_id)
        if not needs:
            raise NotFound("no file for this photo")
        return 409, {"error": "no drive holding this photo is connected", "needs": needs}
    ext = src.suffix.lower()
    if ext in PLAIN_IMAGE_EXTS:
        return _full_reply(src.read_bytes(), "image/png" if ext == ".png" else "image/jpeg", True, _image_size(src))
    attempts = ([(("JpgFromRaw",), True), (("PreviewImage",), False)] if ext not in (".tif", ".tiff", ".psd", ".psb")
                else [(("PreviewImage", "JpgFromRaw"), False)])
    for tags, is_full in attempts:
        data = preview.extract_preview(src, tags=tags)
        if data is None:
            continue
        try:
            cdir.mkdir(parents=True, exist_ok=True)
            tmp = cdir / f"{group_id}.tmp"
            tmp.write_bytes(data)
            os.replace(tmp, cdir / (f"{group_id}.full.jpg" if is_full else f"{group_id}.part.jpg"))
        except OSError as exc:
            logger.warning("could not cache full preview for group %s: %s", group_id, exc)
        return _full_reply(data, "image/jpeg", is_full, _image_size(data))
    raise NotFound("no embedded preview")


def _settings(conn) -> dict:
    out: dict[str, Any] = {}
    for key, default in SETTING_DEFAULTS.items():
        value = repo.get_setting(conn, key, default)
        out[key] = int(value) if key in ("match_window_s", "match_reach_s") else value
    out["review_thumbs"] = _review_thumbs_info(conn)
    return out


def _review_thumbs_info(conn) -> dict:
    """The review-thumbs setting plus what the Settings section shows: frames in the catalog, thumbs on disk, and
    whether the folder is acceptable (pv-1)."""
    cfg = previews.review_config(conn)
    error = None
    count = 0
    try:
        count = len(previews.review_ids(previews.review_folder(cfg["folder"], VOLUMES_ROOT)))
    except ValueError as exc:
        error = str(exc)
    frames = conn.execute("SELECT COUNT(*) FROM capture_groups").fetchone()[0]
    return {**cfg, "frames": int(frames), "count": count, "bytes_each": previews.REVIEW_TARGET_BYTES,
            "error": error}


def _post_review_thumbs(conn, value: Any) -> str:
    if not isinstance(value, dict) or set(value) - {"enabled", "folder"}:
        raise BadRequest("review_thumbs must be {enabled, folder}")
    cur = previews.review_config(conn)
    enabled = value.get("enabled", cur["enabled"])
    folder = value.get("folder", cur["folder"])
    if not isinstance(enabled, bool):
        raise BadRequest("review_thumbs.enabled must be true or false")
    if not isinstance(folder, str) or not folder.strip():
        raise BadRequest("review_thumbs.folder must be a folder path such as ~/Sandbox/PhotoVault Thumbs")
    try:
        previews.review_folder(folder.strip(), VOLUMES_ROOT)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return json.dumps({"enabled": enabled, "folder": folder.strip()})


def _reveal_review_thumbs(conn) -> dict:
    if sys.platform != "darwin":
        raise BadRequest("Show in Finder works on macOS only")
    try:
        folder = previews.review_folder(previews.review_config(conn)["folder"], VOLUMES_ROOT)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    folder.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(["open", str(folder)], capture_output=True, text=True, timeout=10, check=False)
    if result.returncode != 0:
        raise Conflict(result.stderr.strip() or "Finder did not open")
    return {"opened": str(folder)}


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
        elif key in ("place_name", "city", "state", "country", "creator", "copyright", "title"):
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


def _pause_run(app: WebApp, conn, pid: int) -> dict:
    """Pause a run this server did not start (a terminal run, or a task of an earlier `serve`, which now runs headless):
    SIGINT, the same signal Pause sends to a task. Only a PhotoVault run listed in the activity snapshot is signalled."""
    snap = activity.snapshot(conn, app.home, server_pid=os.getpid())
    if not any(e.get("pid") == pid for e in snap["activity"]):
        raise NotFound(f"no PhotoVault run with pid {pid}")
    try:
        os.kill(pid, signal.SIGINT)
    except ProcessLookupError as exc:
        raise Conflict(f"run {pid} already exited") from exc
    return {"pid": pid, "state": "pausing"}


def _route(app: WebApp, conn, method: str, path: str, query: dict, body: Any) -> tuple[int, Any]:
    m = re.fullmatch(r"/api/activity/(\d+)/pause", path)
    if m:
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return 200, _pause_run(app, conn, int(m.group(1)))
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

    m = re.fullmatch(r"/api/body-clock/(\d+)/delete", path)
    if m:
        if method != "POST":
            return 405, {"error": "method not allowed"}
        with repo.transaction(conn):
            if not repo.delete_body_clock(conn, int(m.group(1))):
                raise NotFound("unknown body clock row")
        return 200, {"deleted": int(m.group(1))}

    m = re.fullmatch(r"/api/groups/(\d+)/places", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        window = _int_param(query, "window_s", 3600, 60, 86400)
        try:
            return 200, {"places": places_near(conn, int(m.group(1)), window_s=window)}
        except ValueError as exc:
            raise NotFound(str(exc)) from exc

    m = re.fullmatch(r"/api/groups/(\d+)/detail", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        return 200, _group_detail(app, conn, int(m.group(1)))

    m = re.fullmatch(r"/api/groups/(\d+)/(suggestions|thumb|preview)", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        group_id = int(m.group(1))
        if m.group(2) == "preview" and query.get("full", [""])[0] == "1":
            return _full_preview(app, conn, group_id)
        if m.group(2) in ("thumb", "preview"):
            return 200, _thumb(app, conn, group_id, large=m.group(2) == "preview")
        try:
            window = int(repo.get_setting(conn, "match_window_s", SETTING_DEFAULTS["match_window_s"]))
            reach = int(repo.get_setting(conn, "match_reach_s", SETTING_DEFAULTS["match_reach_s"]))
            found = suggest(conn, group_id, window_s=window, reach_s=reach)
        except ValueError as exc:
            raise NotFound(str(exc)) from exc
        return 200, {"suggestions": found}

    m = re.fullmatch(r"/api/volumes/([^/]+)(?:/(files|reveal|label|eject|photo))?", path)
    if m:
        return _volume_route(app, conn, method, m.group(1), m.group(2), query, body)

    if path == "/api/cards":
        if method != "GET":
            return 405, {"error": "method not allowed"}
        return 200, cards.list_cards(conn, _drive_list(conn, app.volumes_root))

    m = re.fullmatch(r"/api/actions/([^/]+)/(dismiss|snooze|restore)", path)
    if m:
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return 200, _action_state(app, conn, unquote(m.group(1)), m.group(2), body)

    m = re.fullmatch(r"/api/cards/([^/]+)/files", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        return 200, _card_files(app, conn, m.group(1), query)

    m = re.fullmatch(r"/api/cards/([^/]+)/make-green", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        try:
            return 200, backup.plan_make_green(conn, None, card_volume_id=m.group(1),
                                               mounted=_mounted_paths(app, conn), home=app.home)
        except backup.BackupRefused as exc:
            raise NotFound(str(exc)) from exc

    m = re.fullmatch(r"/api/cards/([^/]+)/targets", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        return 200, _card_targets(app, conn, m.group(1), query)

    m = re.fullmatch(r"/api/assets/(\d+)/copies", path)
    if m:
        if method != "GET":
            return 405, {"error": "method not allowed"}
        return 200, _asset_copies(app, conn, int(m.group(1)))

    routes: dict[tuple[str, str], Any] = {
        ("GET", "/api/drives"): lambda: {"drives": _drive_list(conn, app.volumes_root)},
        ("GET", "/api/drives/usage"): lambda: {"usage": _drives_usage(conn)},
        ("POST", "/api/drives/ignore"): lambda: _post_ignore_drive(conn, body),
        ("GET", "/api/jobs"): lambda: {"jobs": [dict(r) for r in repo.list_jobs(
            conn, _int_param(query, "limit", 20, 1, 200))]},
        ("POST", "/api/run"): lambda: _run(app, body),
        ("GET", "/api/tasks"): lambda: {"tasks": app.runner.summaries()},
        ("GET", "/api/activity"): lambda: activity.snapshot(conn, app.home, server_pid=os.getpid()),
        ("GET", "/api/actions"): lambda: _actions(app, conn, query),
        ("GET", "/api/sleep-when-done"): lambda: app.sleep_when_done().status(),
        ("POST", "/api/sleep-when-done"): lambda: app.sleep_when_done().set(bool(_body_dict(body).get("enabled"))),
        ("GET", "/api/shoots/plan"): lambda: _shoots_plan(app),
        ("POST", "/api/shoots/plan"): lambda: {"task_id": app.runner.start(build_argv("shoots_plan", _body_dict(
            body or {})))},
        ("GET", "/api/shoots/decisions"): lambda: _shoot_decisions(conn, "list", query, body),
        ("POST", "/api/shoots/decisions"): lambda: _shoot_decisions(conn, "add", query, body),
        ("POST", "/api/shoots/decisions/delete"): lambda: _shoot_decisions(conn, "delete", query, body),
        ("POST", "/api/shoots/apply"): lambda: _shoots_task(app, "shoots_apply", body),
        ("POST", "/api/shoots/undo"): lambda: _shoots_task(app, "shoots_undo", body),
        ("GET", "/api/shoots/applies"): lambda: {"applies": shoots.list_applies(conn)},
        ("GET", "/api/shoots"): lambda: {"shoots": [dict(r) for r in repo.list_shoots(conn)]},
        ("GET", "/api/groups"): lambda: _groups(conn, query, app),
        ("GET", "/api/photo-days"): lambda: _photo_days(conn, query),
        ("GET", "/api/outline"): lambda: _outline(conn, app, query),
        ("POST", "/api/groups/meta"): lambda: _replan_after(app, _groups_meta(conn, body)),
        ("GET", "/api/clock-suggestions"): lambda: {"suggestions": clock.suggest_offsets(conn)},
        ("GET", "/api/body-clock"): lambda: {"rows": [dict(r) for r in repo.list_body_clock(conn)]},
        ("POST", "/api/body-clock"): lambda: _post_body_clock(conn, body),
        ("GET", "/api/library/checklist"): lambda: _library_checklist(app, conn),
        ("GET", "/api/settings"): lambda: {"settings": _settings(conn)},
        ("POST", "/api/settings"): lambda: _post_settings(conn, body),
        ("POST", "/api/settings/review-thumbs/reveal"): lambda: _reveal_review_thumbs(conn),
    }
    handler = routes.get((method, path))
    if handler is not None:
        return 200, handler()
    if any(p == path for _, p in routes):
        return 405, {"error": "method not allowed"}
    return 404, {"error": "unknown endpoint"}


ACTIONS_TTL_S = 30.0
_ACTIONS_CACHE: dict[str, tuple[float, tuple, list[dict]]] = {}  # catalog path -> (computed at, key, actions)


def _mounted_paths(app: WebApp, conn) -> dict[str, str]:
    """``volume_id -> mount path`` of every registered drive plugged in now (a read-only scan)."""
    try:
        rows = scan_mounts(conn, app.volumes_root, _NullEmitter())
    except OSError:
        return {}
    return {m["volume_id"]: m["mount"] for m in rows if m["volume_id"]}


def _actions(app: WebApp, conn, query: dict) -> dict:
    """``GET /api/actions`` (act-1, D-084). The computed list is cached for 30 s and dropped when the mounted drives
    change or a job starts, ends or changes state; dismiss and snooze are applied on every read."""
    now = datetime.now(UTC)
    mounted = _mounted_paths(app, conn)
    jobs = tuple(conn.execute(
        "SELECT COUNT(*), MAX(job_id), MAX(finished_at), SUM(state = 'running'), SUM(state = 'paused'), "
        "SUM(state = 'failed') FROM jobs").fetchone())
    key = (tuple(sorted(mounted.items())), jobs)
    hit = _ACTIONS_CACHE.get(str(app.catalog_path))
    if hit and hit[1] == key and time.monotonic() - hit[0] < ACTIONS_TTL_S:
        found = hit[2]
    else:
        found = actions.compute(conn, now=now, mounted=mounted, home=app.home)
        _ACTIONS_CACHE[str(app.catalog_path)] = (time.monotonic(), key, found)
    return actions.build_response(found, actions.load_state(conn), now)


def _action_state(app: WebApp, conn, action_id: str, verb: str, body: Any) -> dict:
    """Dismiss, snooze or restore one action. Stored in the `actions_state` setting only."""
    state = actions.load_state(conn)
    if verb == "restore":
        state.pop(action_id, None)
    else:
        known = _ACTIONS_CACHE.get(str(app.catalog_path))
        if known is None or all(a["id"] != action_id for a in known[2]):
            known = (0.0, (), actions.compute(conn, now=datetime.now(UTC), mounted=_mounted_paths(app, conn),
                                              home=app.home))
        if all(a["id"] != action_id for a in known[2]):
            raise NotFound(f"no action {action_id!r}")
        if verb == "dismiss":
            state[action_id] = {"dismissed": True}
        else:
            days = _body_dict(body or {}).get("days", 7)
            if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
                raise BadRequest("days must be a whole number from 1 to 365")
            state[action_id] = {"dismissed": False,
                                "until": (datetime.now(UTC) + timedelta(days=days)).isoformat(timespec="seconds")}
    with repo.transaction(conn):
        actions.save_state(conn, state)
    return {"id": action_id, "state": state.get(action_id)}


def _shoots_plan(app: WebApp) -> dict:
    """``GET /api/shoots/plan`` (sh-3): the newest ``<home>/plans/shoots-*.json``, read-only. ``PHOTOVAULT_DEV_PLAN=<path>``
    serves that file instead (development and the fixture). 404 when there is none; ``POST`` (or `shoots plan`)
    makes one."""
    path = _plan_path(app)
    if path is None:
        raise NotFound("no plan yet")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NotFound("no plan yet") from exc
    except (OSError, ValueError) as exc:
        raise BadRequest(f"the plan file could not be read: {exc}") from exc


_PLAN_CACHE: dict[str, tuple[tuple, dict]] = {}


def _plan_cached(app: WebApp) -> dict | None:
    """The newest plan parsed once per file version (path, mtime, size), or None: the outline's input (rv-46)."""
    path = _plan_path(app)
    if path is None:
        return None
    try:
        st = path.stat()
        stamp = (str(path), st.st_mtime_ns, st.st_size)
        hit = _PLAN_CACHE.get("plan")
        if hit is not None and hit[0] == stamp:
            return hit[1]
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(plan, dict):
        return None
    _PLAN_CACHE["plan"] = (stamp, plan)
    return plan


def _outline(conn, app: WebApp, query: dict) -> dict:
    """``GET /api/outline?volume_id=&missing=`` (rv-46, D-134): trips, shoots and loose days, read-only."""
    try:
        return outline.outline(conn, _plan_cached(app), volume_id=_opt_param(query, "volume_id"),
                               missing=_opt_param(query, "missing"))
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc


def _plan_path(app: WebApp) -> Path | None:
    """The plan `GET /api/shoots/plan` serves: the dev override, else the newest ``<home>/plans/shoots-*.json``."""
    override = os.environ.get("PHOTOVAULT_DEV_PLAN")
    if override:
        return Path(override)
    plans = app.home / "plans"
    found = sorted(plans.glob("shoots-*.json")) if plans.is_dir() else []
    return found[-1] if found else None


# --------------------------------------------------------------------------- library checklist (ob-1)

def _library_checklist(app: WebApp, conn) -> dict:
    """``GET /api/library/checklist`` (ob-1, review sh-8 §3): what the library has so far. Read-only: one row per known
    drive that is not ignored (indexed, hashed files, review thumbs), then the phone import, the metadata scan and the
    newest shoots plan. Review thumbs come from one listing of the folder, never per-file stats."""
    ignored = set(_ignored_drives(conn))
    connected = {r["volume_id"] for r in _drive_list(conn, app.volumes_root) if r.get("mounted") and r.get("volume_id")}
    cfg = previews.review_config(conn)
    thumb_ids: set[int] = set()
    thumb_error = None
    try:
        thumb_ids = previews.review_ids(previews.review_folder(cfg["folder"], VOLUMES_ROOT))
    except ValueError as exc:
        thumb_error = str(exc)

    drives = []
    for v in repo.list_volumes(conn):
        vid = v["volume_id"]
        if vid in ignored:
            continue
        files = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(asset_id IS NOT NULL), 0) AS hashed FROM instances "
            "WHERE volume_id = ? AND state IN ('indexed', 'verified')", (vid,)).fetchone()
        indexed_job = conn.execute(
            "SELECT 1 FROM jobs WHERE source_volume_id = ? AND kind = 'index_volume' AND state = 'done' LIMIT 1",
            (vid,)).fetchone()
        groups = {r["group_id"] for r in conn.execute(
            "SELECT DISTINCT a.group_id FROM instances i JOIN assets a ON a.asset_id = i.asset_id "
            "AND a.group_role = 'primary' WHERE i.volume_id = ? AND i.state IN ('indexed', 'verified') "
            "AND a.group_id IS NOT NULL", (vid,))}
        drives.append({
            "volume_id": vid, "label": v["label"], "role": v["role"], "connected": vid in connected,
            "indexed": bool(indexed_job) or int(files["total"]) > 0,
            "hashed_files": int(files["hashed"]), "total_files": int(files["total"]),
            "thumbs": {"have": len(groups & thumb_ids), "groups": len(groups)},
        })

    phone_ids = tuple(PHONE_SOURCES)
    marks = ",".join("?" * len(phone_ids))
    phone = conn.execute(
        f"SELECT COUNT(*) AS points, COALESCE(SUM(a.point_id IS NOT NULL), 0) AS analysed FROM geo_points g "
        f"LEFT JOIN phone_analysis a ON a.point_id = g.point_id WHERE g.source IN ({marks})", phone_ids).fetchone()
    meta = conn.execute(
        "SELECT (SELECT COUNT(*) FROM capture_groups) AS groups, (SELECT COUNT(*) FROM group_meta m "
        "JOIN capture_groups c ON c.group_id = m.group_id) AS scanned").fetchone()

    plan = None
    path = _plan_path(app)
    if path is not None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            shoots_list = data.get("shoots") or []
            # A locked shoot is one already applied (sh-1); the rest still need confirming.
            plan = {"plan_id": data.get("plan_id"), "created_at": data.get("created_at"),
                    "shoots": len(shoots_list),
                    "unconfirmed": sum(1 for s in shoots_list if not s.get("locked"))}
        except (OSError, ValueError, AttributeError):
            plan = None
    return {
        "drives": drives,
        "thumbs_enabled": bool(cfg["enabled"]), "thumbs_error": thumb_error,
        "phone": {"points": int(phone["points"]), "analysed": int(phone["analysed"])},
        "meta_scanned": {"groups": int(meta["scanned"]), "total": int(meta["groups"])},
        "plan": plan,
    }


# With PHOTOVAULT_DEV_PLAN the review runs on a fixture whose ids mean nothing in the catalog, and a rejected name
# would join the real stoplist, so its decisions live in memory for the life of the server instead.
_DEV_DECISIONS: dict[str, sqlite3.Connection] = {}
_DEV_LOCK = threading.Lock()


def _dev_decisions_conn(override: str) -> sqlite3.Connection:
    conn = _DEV_DECISIONS.get(override)
    if conn is None:
        conn = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE shoot_plan_decisions (decision_id INTEGER PRIMARY KEY, proposal_id TEXT NOT NULL, "
                     "kind TEXT NOT NULL, payload_json TEXT NOT NULL, decided_at TEXT NOT NULL)")
        _DEV_DECISIONS[override] = conn
    return conn


def _shoots_task(app: WebApp, cmd: str, body: Any) -> dict:
    """sh-4: Apply and Undo run as tasks (shown in Tasks). ``/api/shoots/undo`` takes ``{"job_id": <apply id>}``."""
    if os.environ.get("PHOTOVAULT_DEV_PLAN"):
        raise BadRequest("this is the fixture plan (PHOTOVAULT_DEV_PLAN): Apply and Undo are off")
    args = dict(_body_dict(body or {}))
    if cmd == "shoots_undo" and "job_id" in args:
        args["apply_id"] = args.pop("job_id")
    return {"task_id": app.runner.start(build_argv(cmd, args))}


def _shoot_decisions(conn, verb: str, query: dict, body: Any) -> dict:
    """sh-2: the review decisions. ``GET ?after=N`` lists those after id N; ``POST`` stores one
    ``{proposal_id, kind, payload}`` or ``{"decisions": [...]}`` (all or none); ``POST …/delete
    {"decision_ids": [...]}`` undoes them."""
    override = os.environ.get("PHOTOVAULT_DEV_PLAN")
    with _DEV_LOCK if override else _nullcontext():
        target = _dev_decisions_conn(override) if override else conn
        if verb == "list":
            return {"decisions": shoots.list_decisions(target, _int_param(query, "after", 0, 0, 2**62)),
                    "dev": bool(override)}
        body = _body_dict(body)
        if verb == "add":
            items = body.get("decisions") if "decisions" in body else [body]
            if not isinstance(items, list) or not items or len(items) > 5000:
                raise BadRequest("decisions must be a list of 1 to 5000 decisions")
            try:
                with repo.transaction(target):
                    return {"decisions": shoots.add_decisions(target, items)}
            except ValueError as exc:
                raise BadRequest(str(exc)) from exc
        ids = body.get("decision_ids")
        if not isinstance(ids, list) or not ids or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            raise BadRequest("decision_ids must be a non-empty list of whole numbers")
        with repo.transaction(target):
            return {"deleted": shoots.delete_decisions(target, ids)}


FILE_STATES = ("indexed", "verified", "mismatch", "missing")
RUN_LABELS = {"index_volume": "Index", "baseline_hash": "Hash"}


def _volume_route(app: WebApp, conn, method: str, volume_id: str, sub: str | None, query: dict,
                  body: Any) -> tuple[int, Any]:
    if sub == "reveal":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return 200, _reveal(app, conn, volume_id, _body_dict(body))
    if sub == "label":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return 200, _set_label(conn, volume_id, _body_dict(body))
    if sub == "eject":
        if method != "POST":
            return 405, {"error": "method not allowed"}
        return 200, _eject(app, conn, volume_id)
    if method != "GET":
        return 405, {"error": "method not allowed"}
    if repo.get_volume(conn, volume_id) is None:
        raise NotFound("unknown drive")
    if sub == "photo":
        return 200, _file_photo(conn, volume_id, query)
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
        shoot_key = _opt_param(query, "shoot_key")
        try:
            group_ids = (outline.resolve_key(conn, _plan_cached(app), shoot_key, volume_id=volume_id)
                         if shoot_key not in (None, outline.OTHER_KEY) else None)
        except ValueError as exc:
            raise BadRequest(str(exc)) from exc
        filters = {"state": state, "copies": copies, "other_volume_id": other, "camera": camera,
                   "group_ids": group_ids, "ungrouped": shoot_key == outline.OTHER_KEY}
        rows = repo.volume_files(conn, volume_id, limit=limit, offset=offset, **filters)
        names = cards.camera_names(conn)
        return 200, {"files": [_file_row(r, names) for r in rows], "limit": limit, "offset": offset,
                     "total": repo.volume_files_count(conn, volume_id, **filters)}
    return 200, _volume_summary(app, conn, volume_id)


VOLUME_LABEL_MAX = 64


def _set_label(conn, volume_id: str, body: dict) -> dict:
    """Give a drive a new nickname in the catalog. The volume on disk keeps its name (renaming that is vn-2)."""
    label = body.get("label")
    if not isinstance(label, str):
        raise BadRequest("label must be a string")
    label = " ".join(label.split())  # trim and collapse whitespace; also drops tabs and newlines
    if not label:
        raise BadRequest("label may not be empty")
    if len(label) > VOLUME_LABEL_MAX:
        raise BadRequest(f"label may be at most {VOLUME_LABEL_MAX} characters")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in label):
        raise BadRequest("label contains a control character")
    with repo.transaction(conn):
        if not repo.set_volume_label(conn, volume_id, label):
            raise NotFound("unknown drive")
    return {"volume_id": volume_id, "label": label}


def _card_targets(app: WebApp, conn, volume_id: str, query: dict) -> dict:
    """``GET /api/cards/<id>/targets[?planned_bytes=N]`` (rec-1, D-103): the connected drives ranked for a copy of
    the card, each with its reasons, and the proposed pair (read drive, trailing-copy drive). Default size: the
    card's bytes that have no verified WORKING/MASTER copy yet."""
    vol = repo.get_volume(conn, volume_id)
    if vol is None or vol["role"] != "CARD":
        raise NotFound("unknown card")
    planned = _int_param(query, "planned_bytes", -1, -1, 2**62)
    if planned < 0:
        planned = targets.card_unbacked_bytes(conn, volume_id)
    return targets.recommend(conn, app.home, _mounted_paths(app, conn), planned_bytes=planned,
                             source_volume_id=volume_id)


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
    shoot_key = _opt_param(query, "shoot_key")
    if shoot_key is not None:  # the files of one shoot (rv-48): every asset of its frames; `other` = no photo group
        ids = json.dumps([f["asset_id"] for f in files if f["asset_id"] is not None])
        if shoot_key == outline.OTHER_KEY:
            grouped = {r[0] for r in conn.execute(
                "SELECT asset_id FROM assets WHERE group_id IS NOT NULL AND asset_id IN (SELECT value FROM json_each(?))",
                (ids,))}
            files = [f for f in files if f["asset_id"] not in grouped]
        else:
            try:
                gids = outline.resolve_key(conn, _plan_cached(app), shoot_key)
            except ValueError as exc:
                raise BadRequest(str(exc)) from exc
            keep = {r[0] for r in conn.execute(
                "SELECT asset_id FROM assets WHERE group_id IN (SELECT value FROM json_each(?))", (json.dumps(gids),))}
            files = [f for f in files if f["asset_id"] in keep]
    return {"card": {"volume_id": volume_id, "label": vol["label"], "mounted": mount is not None,
                     "mount": str(mount) if mount else None, "last_seen_at": vol["last_seen_at"],
                     "cleared_at": cleared_at(conn, volume_id),
                     "test_card": cards.test_card_badge(conn, volume_id), **summary},
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


def _file_photo(conn, volume_id: str, query: dict) -> dict:
    """The photo (capture group) a file on a drive or card belongs to, and its day in the Photos timeline (rv-27).

    A hashed file links through its asset; a file only indexed so far through its index job's item. The day is the
    wall date the timeline groups by (``repo._group_filter_sql``): an offset shoot's wall date, else the capture date.
    """
    rel_path = _opt_param(query, "rel_path")
    if rel_path is None:
        raise BadRequest("rel_path is required")
    key = repo.path_key(rel_path)
    row = conn.execute(
        """SELECT a.group_id FROM instances i JOIN assets a ON a.asset_id = i.asset_id
           WHERE i.volume_id = ? AND i.rel_path_key = ? AND a.group_id IS NOT NULL""", (volume_id, key)).fetchone()
    if row is None:
        for r in conn.execute(
                """SELECT ji.src_rel, ji.group_id FROM job_items ji
                   JOIN jobs j ON j.job_id = ji.job_id AND j.kind = 'index_volume'
                   WHERE j.source_volume_id = ? AND ji.group_id IS NOT NULL AND ji.src_rel = ?
                   ORDER BY ji.job_id DESC""", (volume_id, rel_path)):
            row = r
            break
    if row is None:
        raise NotFound("this file is not part of a photo in the timeline")
    gid = int(row["group_id"])
    g = conn.execute("SELECT capture_time FROM capture_groups WHERE group_id = ?", (gid,)).fetchone()
    if g is None:
        raise NotFound("this file is not part of a photo in the timeline")
    day = repo.offset_shoot_wall_dates(conn).get(gid) or (g["capture_time"][:10] if g["capture_time"] else repo.UNDATED)
    return {"group_id": gid, "day": day}


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


def _drives_usage(conn) -> dict[str, dict]:
    """Per drive, the bytes of the drive page's space bar (idx-13): photography, not photography, unaccounted, used and
    capacity. For the sidebar's space bars; a drive no index has counted yet is left out. Fetched on demand, not with
    every /api/drives poll, since it sums each drive's instances."""
    out: dict[str, dict] = {}
    for v in repo.list_volumes(conn):
        vid = v["volume_id"]
        states = {r["state"]: {"files": r["files"], "bytes": r["bytes"]} for r in repo.volume_state_counts(conn, vid)}
        u = _volume_usage(conn, vid, states)
        if u:
            out[vid] = {"photo": u["photo"]["bytes"], "non_photo": u["non_photo"]["bytes"],
                        "unaccounted": u["unaccounted"], "used": u["used"], "capacity": u["capacity"]}
    return out


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


def _uses_mount(words: list[str], mount: str) -> bool:
    """A command's words name `mount` itself or a path on it."""
    m = mount.rstrip("/")
    return any(w.rstrip("/") == m or w.startswith(m + "/") for w in words)


def _eject(app: WebApp, conn, volume_id: str) -> dict:
    """Eject a drive the way Finder does (`diskutil eject`, rv-43). It writes no file: macOS unmounts the volume (and
    the other volumes of its disk) and can then be unplugged. Refused for the Mac folder, for anything outside the
    volumes root, and while a task (UI or terminal) names the drive; macOS itself refuses while files are open."""
    if sys.platform != "darwin":
        raise BadRequest("Eject works on macOS only")
    drive = _drive_row(app, conn, volume_id)
    if drive is None:
        raise NotFound("unknown drive")
    if drive.get("local"):
        raise BadRequest("the Mac folder is not a drive; there is nothing to eject")
    if not drive["mounted"] or not drive["mount"]:
        raise Conflict("this drive is not connected")
    mount = Path(drive["mount"])
    root = Path(app.volumes_root).resolve()
    if not mount.resolve().is_relative_to(root) or mount.resolve() == root:
        raise BadRequest("only drives mounted under the volumes folder can be ejected")
    name = drive.get("label") or mount.name
    for s in app.runner.summaries():
        if s["state"] == "running" and _uses_mount(s["argv"], str(mount)):
            raise Conflict(f"a task is using {name}. Pause it or let it finish, then eject.")
    for p in activity.list_processes() or []:
        if _uses_mount(p.words, str(mount)):
            raise Conflict(f"a run started in a terminal is using {name} (pid {p.pid}). Stop it, then eject.")
    try:
        result = subprocess.run(["diskutil", "eject", str(mount)], capture_output=True, text=True, timeout=60,
                                check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Conflict(f"could not eject {name}: {exc}") from exc
    if result.returncode != 0:
        text = (result.stderr.strip() or result.stdout.strip() or "macOS did not eject it")
        raise Conflict(f"could not eject {name}: {text}. Close any app using it (Finder windows, Lightroom) and try again.")
    return {"ejected": str(mount), "label": name}


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


def _groups(conn, query: dict, app: WebApp | None = None) -> dict:
    volume_id = _opt_param(query, "volume_id")
    shoot_raw = _opt_param(query, "shoot_id")
    try:
        shoot_id = int(shoot_raw) if shoot_raw is not None else None
    except ValueError as exc:
        raise BadRequest("shoot_id must be an integer") from exc
    missing = _opt_param(query, "missing")
    day = _opt_param(query, "day")
    after = _opt_param(query, "after")
    before = _opt_param(query, "before")
    limit = _int_param(query, "limit", 200, 1, 1000)
    offset = _int_param(query, "offset", 0, 0, 2**62)
    shoot_key = _opt_param(query, "shoot_key")
    try:
        group_ids = (outline.resolve_key(conn, _plan_cached(app), shoot_key, volume_id=volume_id, missing=missing)
                     if shoot_key is not None and app is not None else None)
        rows = repo.list_groups(conn, volume_id=volume_id, shoot_id=shoot_id, missing=missing, day=day, after=after,
                                before=before, group_ids=group_ids, limit=limit,
                                offset=offset)
        counts = repo.missing_counts(conn, volume_id=volume_id)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    default_offset = repo.get_setting(conn, "default_offset", SETTING_DEFAULTS["default_offset"]) or "+00:00"
    groups = [_group_row(r) for r in rows]
    clocks = timemodel.BodyClocks.load(conn)  # once per request (sh-0b)
    for g in groups:  # the instant (UTC) and the wall clock, with how the instant was found (sh-0b, D-080)
        try:
            info = timemodel.instant_info(g, default_offset, clocks)
        except ValueError:
            info = None
        g["utc_time"] = utc_text(info.when) if info else None
        g["time_provenance"] = info.provenance if info else None
        g["time_correction_s"] = info.correction_s if info else 0
        g["time_user_shift"] = info.user_shift if info else False
        g["wall_time"] = timemodel.wall_clock(g, info.when if info else None)
        g["wall_date"] = g["wall_time"][:10] if g["wall_time"] else None
    unhashed = [g["group_id"] for g in groups if g["rel_path"] is None]
    if unhashed:  # files indexed but not hashed yet: located through their index job (rv-7)
        found = repo.indexed_primary_files(conn, unhashed)
        for g in groups:
            loc = (found.get(g["group_id"]) or [None])[0]
            if loc and g["rel_path"] is None:
                if volume_id is not None:
                    loc = next((f for f in found[g["group_id"]] if f["volume_id"] == volume_id), loc)
                g["rel_path"], g["volume_id"] = loc["rel_path"], loc["volume_id"]
    try:
        cfg = load_config()
    except ConfigError:
        cfg = None
    for g in groups:  # the D-005 name, from catalog fields only (rv-18)
        g["vault_name"] = naming.vault_name(cfg, g) if cfg is not None else None
    _mark_full_available(app, conn, groups)
    return {"groups": groups, "missing_counts": counts}


def _mounted_volume_ids(app: WebApp | None, conn) -> set[str]:
    if app is None:
        return set()
    try:
        return {m["volume_id"] for m in scan_mounts(conn, app.volumes_root, _NullEmitter()) if m["volume_id"]}
    except OSError:
        return set()


def _mark_full_available(app: WebApp | None, conn, groups: list[dict]) -> None:
    """``full_available`` (a primary file sits on a drive mounted now) and ``held_on`` (up to 3 drive labels) on every
    row, from one query for all rows plus one for files not hashed yet (rv-23)."""
    ids = [g["group_id"] for g in groups]
    held: dict[int, set[str]] = {i: set() for i in ids}
    if ids:
        marks = ",".join("?" * len(ids))
        for r in conn.execute(
            f"""SELECT a.group_id, i.volume_id FROM assets a
                JOIN instances i ON i.asset_id = a.asset_id AND i.state IN ('indexed', 'verified')
                WHERE a.group_role = 'primary' AND a.group_id IN ({marks})""", ids):
            held[r["group_id"]].add(r["volume_id"])
        for gid, files in repo.indexed_primary_files(conn, [i for i in ids if not held[i]]).items():
            held[gid].update(f["volume_id"] for f in files)
    labels = {v["volume_id"]: v["label"] for v in repo.list_volumes(conn)}
    mounted = _mounted_volume_ids(app, conn)
    for g in groups:
        vols = held[g["group_id"]]
        g["full_available"] = bool(vols & mounted)
        g["held_on"] = sorted({labels.get(v) or "Drive" for v in vols})[:3]
        g["held_volumes"] = sorted(vols)  # so the UI recomputes on a mount change without refetching


def _photo_days(conn, query: dict) -> dict:
    """The Photos timeline (rv-7): days with photos, newest first, and the counts of what is missing."""
    volume_id = _opt_param(query, "volume_id")
    missing = _opt_param(query, "missing")
    shoot_raw = _opt_param(query, "shoot_id")
    try:
        shoot_id = int(shoot_raw) if shoot_raw is not None else None
    except ValueError as exc:
        raise BadRequest("shoot_id must be an integer") from exc
    try:
        days = repo.list_photo_days(conn, volume_id=volume_id, missing=missing, shoot_id=shoot_id)
        counts = repo.missing_counts(conn, volume_id=volume_id)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    return {"days": days, "missing_counts": counts}


def _replan_after(app: WebApp, result: Any) -> Any:
    app.schedule_replan()
    return result


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


def _post_body_clock(conn, body: Any) -> dict:
    """sp-7 Apply (sh-0b): an accepted ``body_clock`` row, the correction for one body over a UTC range."""
    body = _body_dict(body)
    body_id, corr = body.get("body_id"), body.get("correction_s")
    for name, value in (("body_id", body_id), ("correction_s", corr)):
        if isinstance(value, bool) or not isinstance(value, int):
            raise BadRequest(f"{name} must be an integer")
    source = body.get("source", "phone")
    if source not in ("phone", "gps", "user"):
        raise BadRequest("source must be phone, gps or user")
    ends = []
    for name in ("start_utc", "end_utc"):
        try:
            when = datetime.fromisoformat(str(body.get(name)))
            ends.append(utc_text(when if when.tzinfo else when.replace(tzinfo=UTC)))
        except (TypeError, ValueError) as exc:
            raise BadRequest(f"{name} must be an ISO-8601 time") from exc
    if ends[0] > ends[1]:
        raise BadRequest("start_utc must not be after end_utc")
    if conn.execute("SELECT 1 FROM bodies WHERE body_id = ?", (body_id,)).fetchone() is None:
        raise BadRequest("unknown body_id")
    with repo.transaction(conn):
        clock_id = repo.add_body_clock(conn, body_id=body_id, start_utc=ends[0], end_utc=ends[1],
                                       correction_s=corr, source=source)
    return {"clock_id": clock_id}


def _post_settings(conn, body: Any) -> dict:
    body = _body_dict(body)
    unknown = set(body) - set(SETTING_DEFAULTS) - {"review_thumbs"}
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
        elif key == "review_thumbs":
            values[key] = _post_review_thumbs(conn, value)
        elif key == "mac_folder":
            if not isinstance(value, str) or not value.strip():
                raise BadRequest("mac_folder must be a folder path such as ~/Sandbox")
            if not volumes.is_folder_volume(Path(value.strip()).expanduser()):
                raise BadRequest("mac_folder must be an existing folder inside your home folder (not home itself)")
            values[key] = value.strip()
        else:  # match_window_s, match_reach_s
            if isinstance(value, bool) or not isinstance(value, (int, str)):
                raise BadRequest(f"{key} must be a whole number of seconds")
            try:
                seconds = int(value)
            except ValueError as exc:
                raise BadRequest(f"{key} must be a whole number of seconds") from exc
            if not 0 <= seconds <= 86_400:
                raise BadRequest(f"{key} must be between 0 and 86400")
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
            if method == "GET" and path == "/api/generation":
                # A cheap poll in place of /api/events: a held stream per tab used up the browser's six connections
                # to this host, so every other request queued (the UI stuck on loading).
                watcher = self.server.app.watcher
                with watcher.cond:
                    return self._send_json(200, {"generation": watcher.generation})
            if method == "GET" and path in ("/", "/next", "/next/"):  # the UI (D-059), a Vite build of web/
                return self._static("next/index.html")
            if method == "GET" and path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path.startswith("/api/"):
                body = self._read_body() if method == "POST" else None
                query = parse_qs(url.query, keep_blank_values=True)
                status, payload = route(self.server.app, method, path, query, body)
                if isinstance(payload, Raw):
                    return self._send(status, payload.body, payload.content_type, payload.headers)
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

    def _send(self, status: int, body: bytes, content_type: str, extra: tuple = ()) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for k, v in extra:
            self.send_header(k, v)
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
