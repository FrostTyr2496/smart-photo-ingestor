"""Engine runs the web UI did not start (ui-11): live processes, their progress, and their catalog jobs.

Two sources, both read-only:

- **Progress files.** `ActivityEmitter` (used by the CLI) writes its newest `progress` event to
  `<home>/activity/<pid>.json` and removes it at exit. Any viewer can read exact phase, current and total.
- **The catalog.** A run started on older code writes no file. For its `running` job (matched by mount) the
  catalog still tells how far it got: files recorded (index), hashed and left (hash), items done (ingest), and
  the time of the newest write.

Liveness comes from `ps`, so a job left `running` by a run that died is reported as stale, not as live.

**History (rv-30).** At exit, `ActivityEmitter.close` appends one line per finished run (UI or terminal) to
`<home>/activity/history.jsonl`: its words, times, exit state, final `result` and last error. Listing and query
commands are left out. The file is a viewer aid like the progress files: trimmed to the newest `HISTORY_KEEP`
lines, safe to delete, never read by the engine.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

from photovault.protocol import EXIT_PAUSED, Emitter

ACTIVITY_DIR = "activity"
WRITE_EVERY_S = 1.0      # newest progress is written at most this often (and on every phase change)
UNTRACKED = frozenset({"serve"})
FILES_KEPT = 6           # last `item` events kept in the progress file, newest last (rv-34)
PS_ARGV = ("ps", "-axww", "-o", "pid=,ppid=,etime=,stat=,command=")
# Commands with a sub-command: their first two words name the command, then the positional path follows.
GROUP_COMMANDS = frozenset({"cards", "volumes", "meta", "lrcat", "report", "catalog"})
HISTORY_NAME = "history.jsonl"
HISTORY_KEEP = 200       # lines kept after a trim
HISTORY_TRIM_BYTES = 512 * 1024
RESULT_MAX_CHARS = 4000  # a bigger result keeps only its scalar fields
# Commands that list or query and change nothing: not worth a line in the history.
QUIET_COMMANDS = frozenset({"serve", "drives", "jobs", "metrics", "volumes list", "volumes probe", "cards list",
                            "meta missing", "catalog init"})


def _iso(t: float) -> str:
    return datetime.fromtimestamp(t, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def activity_dir(home: Path) -> Path:
    return Path(home) / ACTIVITY_DIR


def history_path(home: Path) -> Path:
    return activity_dir(home) / HISTORY_NAME


def _quiet(words: list[str]) -> bool:
    plain = [w for w in words if not w.startswith("-")]
    return not plain or plain[0] in QUIET_COMMANDS or " ".join(plain[:2]) in QUIET_COMMANDS


def run_state(exit_code: int | None) -> str:
    """The task state for an exit code, as the web runner names it."""
    if exit_code == 0:
        return "done"
    if exit_code == EXIT_PAUSED:
        return "paused"
    return "failed"


def _small_result(fields: dict[str, Any]) -> dict[str, Any]:
    """The result without its lists (row dumps), and only its scalars when still too big."""
    out = {k: v for k, v in fields.items() if not isinstance(v, list)}
    try:
        if len(json.dumps(out, default=str)) <= RESULT_MAX_CHARS:
            return out
    except (TypeError, ValueError):
        pass
    return {k: v for k, v in out.items() if v is None or isinstance(v, (str, int, float, bool))}


def append_history(home: Path, entry: dict[str, Any]) -> None:
    """Append one run to the history; trim it when it grows past `HISTORY_TRIM_BYTES`. Never raises."""
    path = history_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:  # one short write: concurrent runs do not interleave
            fh.write(json.dumps(entry, default=str) + "\n")
        if path.stat().st_size > HISTORY_TRIM_BYTES:
            lines = path.read_text(encoding="utf-8").splitlines()[-HISTORY_KEEP:]
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - a viewer aid must never break a run
        pass


def read_history(home: Path, limit: int = 30) -> list[dict]:
    """The newest `limit` finished runs, newest first. Unreadable lines are skipped."""
    try:
        lines = history_path(home).read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(lines):
        if len(out) >= limit:
            break
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("words"), list):
            out.append(entry)
    return out


# --------------------------------------------------------------------------- writer

class ActivityEmitter(Emitter):
    """An `Emitter` that also keeps `<home>/activity/<pid>.json` current while a command runs.

    Nothing is written until `track()` is called and the first `progress` event arrives, so quick commands leave
    no file. Write failures are ignored: progress for viewers never stops the engine. `close()` removes the file.
    """

    def __init__(self, stream: TextIO = sys.stdout, json_mode: bool = True) -> None:
        super().__init__(stream, json_mode)
        self._path: Path | None = None
        self._state: dict[str, Any] = {}
        self._written = False
        self._last_write = 0.0
        self._home: Path | None = None
        self._result: dict[str, Any] | None = None
        self._error: str | None = None

    def track(self, home: Path, words: list[str]) -> None:
        if not words or words[0] in UNTRACKED:
            return
        now = time.time()
        self._home = Path(home)
        self._path = activity_dir(home) / f"{os.getpid()}.json"
        self._state = {"v": 1, "pid": os.getpid(), "words": [w for w in words if w != "--json"],
                       "started_at": _iso(now), "phase": None, "phase_started_at": None, "progress": None,
                       "warning": None, "warnings": 0, "files": [], "updated_at": None}

    def emit(self, event: str, **fields: Any) -> None:
        super().emit(event, **fields)
        if self._path is None:
            return
        try:
            self._observe(event, fields)
        except Exception:  # noqa: BLE001 - a viewer aid must never break a run
            pass

    def _observe(self, event: str, fields: dict[str, Any]) -> None:
        s = self._state
        now = time.time()
        force = False
        if event == "result":
            self._result = _small_result(fields)
            return
        if event == "error":
            self._error = fields.get("message") or fields.get("code")
        if event == "item":  # the files just handled, so a viewer can name them (rv-34)
            files = s["files"]
            files.append({k: fields[k] for k in ("state", "src", "dst", "error") if fields.get(k) is not None})
            del files[:-FILES_KEPT]
            if s["progress"] is not None and now - self._last_write >= WRITE_EVERY_S:
                self._write(now)
            return
        if event == "progress":
            phase = fields.get("phase")
            if phase != s["phase"]:
                s["phase"], s["phase_started_at"], force = phase, _iso(now), True
            s["progress"] = {k: fields.get(k) for k in ("phase", "current", "total", "message")}
        elif event in ("warning", "error"):
            s["warning"] = fields.get("message") or fields.get("code")
            s["warnings"] += 1
            force = True
        else:
            return
        if s["progress"] is None:
            return  # nothing to show until the first progress event
        if force or now - self._last_write >= WRITE_EVERY_S:
            self._write(now)

    def _write(self, now: float) -> None:
        assert self._path is not None
        self._state["updated_at"] = _iso(now)
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_name(self._path.name + ".tmp")
            tmp.write_text(json.dumps(self._state), encoding="utf-8")
            os.replace(tmp, self._path)
            self._written = True
            self._last_write = now
        except OSError:
            pass

    def close(self, exit_code: int | None = None) -> None:
        """Remove the progress file and add the run to the history (rv-30). `exit_code` None = it crashed."""
        if self._path is not None and self._written:
            try:
                self._path.unlink(missing_ok=True)
            except OSError:
                pass
        if self._path is not None and self._home is not None and not _quiet(self._state.get("words", [])):
            s = self._state
            append_history(self._home, {
                "v": 1, "pid": s["pid"], "words": s["words"], "started_at": s["started_at"],
                "finished_at": _iso(time.time()), "exit_code": exit_code, "state": run_state(exit_code),
                "result": self._result, "error": self._error, "warnings": s["warnings"],
                "warning": s["warning"]})
        self._path = None


# --------------------------------------------------------------------------- processes

@dataclass(frozen=True)
class EngineProcess:
    pid: int
    ppid: int
    elapsed_s: int
    words: list[str]   # best guess from the `ps` command line (paths with spaces are rejoined)
    text: str          # the command line after `photovault`, for matching a job's mount
    stat: str = ""     # `ps` state; "U" (macOS) or "D" (Linux) = waiting on a disk read or write (rv-50)


def parse_etime(value: str) -> int:
    """`ps` elapsed time `[[dd-]hh:]mm:ss` in seconds."""
    days = 0
    if "-" in value:
        d, value = value.split("-", 1)
        days = int(d)
    parts = [int(p) for p in value.split(":")]
    while len(parts) < 3:
        parts.insert(0, 0)
    h, m, s = parts
    return ((days * 24 + h) * 60 + m) * 60 + s


def guess_words(text: str) -> list[str]:
    """Split a `ps` command line after `photovault` into words, keeping a path with spaces as one word.

    `ps` loses the original argv boundaries, so the positional path is everything after the command up to the
    first ` --flag`. A flag's value is one word.
    """
    tokens = [t for t in text.split() if t != "--json"]
    if not tokens:
        return []
    head = 2 if tokens[0] in GROUP_COMMANDS and len(tokens) > 1 else 1
    words = tokens[:head]
    rest = tokens[head:]
    positional: list[str] = []
    while rest and not rest[0].startswith("--"):
        positional.append(rest.pop(0))
    if positional:
        words.append(" ".join(positional))
    words.extend(rest)
    return words


def parse_ps(output: str) -> list[EngineProcess]:
    """Engine processes from `ps -o pid=,ppid=,etime=,stat=,command=` output; `uv` wrappers are left out."""
    procs = []
    for line in output.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(\S+)\s+(\S+)\s+(.*)$", line)
        if not m:
            continue
        command = m.group(5)
        tokens = command.split()
        if not tokens or Path(tokens[0]).name in ("uv", "uvx"):
            continue
        # The engine is `.../bin/photovault ...`, `photovault ...` or `python -m photovault ...`; a bare
        # `photovault` word elsewhere (an argument to grep, say) is not.
        at = next((i for i, tok in enumerate(tokens) if Path(tok).name == "photovault"
                   and (i == 0 or "/" in tok or tokens[i - 1] == "-m")), None)
        if at is None:
            continue
        text = " ".join(tokens[at + 1:])
        try:
            elapsed = parse_etime(m.group(3))
        except ValueError:
            elapsed = 0
        procs.append(EngineProcess(pid=int(m.group(1)), ppid=int(m.group(2)), elapsed_s=elapsed,
                                   words=guess_words(text), text=text, stat=m.group(4)))
    return procs


def list_processes() -> list[EngineProcess] | None:
    """Running engine processes, or None when `ps` cannot be run."""
    try:
        out = subprocess.run(PS_ARGV, capture_output=True, text=True, timeout=5, check=True).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    return parse_ps(out)


# --------------------------------------------------------------------------- catalog

def running_jobs(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT j.job_id, j.kind, j.source_volume_id, j.created_at, j.params_json, v.label
        FROM jobs j LEFT JOIN volumes v ON v.volume_id = j.source_volume_id
        WHERE j.state = 'running' ORDER BY j.job_id DESC
        """
    ).fetchall()
    jobs = []
    for r in rows:
        try:
            params = json.loads(r["params_json"] or "{}")
        except ValueError:
            params = {}
        mounts = [params[k] for k in ("mount", "source_mount", "target_mount") if isinstance(params.get(k), str)]
        jobs.append({"job_id": int(r["job_id"]), "kind": r["kind"], "volume_id": r["source_volume_id"],
                     "label": r["label"], "created_at": r["created_at"], "mounts": mounts})
    return jobs


def job_counts(conn: sqlite3.Connection, job: dict) -> dict:
    """How far a running job got, from the catalog: `current`, `total` (or None), `unit`, `last_write_at`."""
    kind, vid, since = job["kind"], job["volume_id"], job["created_at"]
    if kind == "index_volume":
        r = conn.execute(
            "SELECT COUNT(*) AS n, MAX(last_seen_at) AS last FROM instances WHERE volume_id = ? AND last_seen_at >= ?",
            (vid, since),
        ).fetchone()
        return {"current": r["n"], "total": None, "unit": "files recorded", "last_write_at": r["last"]}
    if kind == "baseline_hash":
        r = conn.execute(
            """
            SELECT SUM(verified_at >= ?) AS done, SUM(state = 'indexed' AND asset_id IS NULL) AS left_,
                   MAX(CASE WHEN verified_at >= ? THEN verified_at END) AS last
            FROM instances WHERE volume_id = ?
            """,
            (since, since, vid),
        ).fetchone()
        done, left = r["done"] or 0, r["left_"] or 0
        return {"current": done, "total": done + left, "unit": "files hashed", "last_write_at": r["last"]}
    if kind in ("card_ingest", "replicate"):
        r = conn.execute(
            """
            SELECT COUNT(*) AS n, SUM(state IN ('committed', 'skipped_dup', 'failed')) AS done,
                   MAX(updated_at) AS last FROM job_items WHERE job_id = ?
            """,
            (job["job_id"],),
        ).fetchone()
        return {"current": r["done"] or 0, "total": r["n"], "unit": "files copied or skipped",
                "last_write_at": r["last"]}
    return {"current": None, "total": None, "unit": "", "last_write_at": None}


def _norm(path: str) -> str:
    return path.rstrip("/") or "/"


def _matches(job: dict, text: str) -> bool:
    return any(_norm(m) and _norm(m) in text for m in job["mounts"])


# --------------------------------------------------------------------------- snapshot

def read_progress_files(home: Path, live_pids: set[int] | None) -> dict[int, dict]:
    """`pid → state` from the activity folder. Files of processes no longer running are deleted."""
    folder = activity_dir(home)
    found: dict[int, dict] = {}
    if not folder.is_dir():
        return found
    for path in folder.glob("*.json"):
        try:
            pid = int(path.stem)
        except ValueError:
            continue
        if live_pids is not None and pid not in live_pids:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            found[pid] = data
    return found


def pause_ownerless(conn: sqlite3.Connection, home: Path, *, processes: list[EngineProcess] | None = None,
                    emit: Emitter | None = None) -> list[int]:
    """Set `running` jobs that no live engine process owns to `paused` (idx-9); returns their ids.

    A job is owned when a live process names its mount or runs `resume <job_id>`. When `ps` cannot be run
    nothing is touched. A `warning` event names each job that was paused.
    """
    procs = list_processes() if processes is None else processes
    if procs is None:
        return []
    files = read_progress_files(home, {p.pid for p in procs})
    jobs = running_jobs(conn)
    claimed: set[int] = set()
    for p in procs:
        f = files.get(p.pid)
        text = " ".join(f["words"]) if f and isinstance(f.get("words"), list) else p.text
        words = p.words
        if len(words) >= 2 and words[0] == "resume" and words[1].isdigit():
            claimed.add(int(words[1]))
        job = next((j for j in jobs if j["job_id"] not in claimed and _matches(j, text)), None)
        if job is not None:
            claimed.add(job["job_id"])
    paused = []
    for j in jobs:
        if j["job_id"] in claimed:
            continue
        conn.execute("UPDATE jobs SET state = 'paused' WHERE job_id = ? AND state = 'running'", (j["job_id"],))
        paused.append(j["job_id"])
        if emit is not None:
            emit.emit("warning", code="job_paused",
                      message=f"job {j['job_id']} ({j['kind']}) was marked running but no process owns it; "
                              f"set to paused (resume with: photovault resume {j['job_id']})")
    if paused:
        conn.commit()
    return paused


def snapshot(conn: sqlite3.Connection, home: Path, *, server_pid: int,
             processes: list[EngineProcess] | None = None, now: float | None = None) -> dict:
    """Engine runs not started by this server, with progress, plus `running` jobs that no process is doing.

    `processes` defaults to `list_processes()`. The server itself and its children (UI tasks, which the UI
    already shows) are left out of `activity`, but still count as live for the stale check.
    """
    procs = list_processes() if processes is None else processes
    now = time.time() if now is None else now
    pause_ownerless(conn, home, processes=procs)
    live = {p.pid for p in procs} if procs is not None else None
    files = read_progress_files(home, live)
    jobs = running_jobs(conn)
    claimed: set[int] = set()
    activity = []
    for p in procs or []:
        f = files.get(p.pid)
        text = " ".join(f["words"]) if f and isinstance(f.get("words"), list) else p.text
        job = next((j for j in jobs if j["job_id"] not in claimed and _matches(j, text)), None)
        if job is not None:
            claimed.add(job["job_id"])
        if p.pid == server_pid or p.ppid == server_pid or (p.words and p.words[0] in UNTRACKED):
            continue  # this server, its own tasks, and any other UI server
        entry: dict[str, Any] = {
            "pid": p.pid,
            "words": f["words"] if f and isinstance(f.get("words"), list) else p.words,
            "started_at": _iso(now - p.elapsed_s),
            "source": "progress_file" if f else "catalog",
            "progress": f.get("progress") if f else None,
            "phase_started_at": f.get("phase_started_at") if f else None,
            "updated_at": f.get("updated_at") if f else None,
            "warning": f.get("warning") if f else None,
            "warnings": f.get("warnings", 0) if f else 0,
            "files": f.get("files") or [] if f else [],
            "disk_wait": p.stat[:1] in ("U", "D"),
            "job": None,
        }
        if job is not None:
            entry["job"] = {k: job[k] for k in ("job_id", "kind", "label", "created_at")} | job_counts(conn, job)
        activity.append(entry)
    stale = []
    if procs is not None:
        stale = [{k: j[k] for k in ("job_id", "kind", "label", "created_at")}
                 for j in jobs if j["job_id"] not in claimed]
    return {"activity": activity, "stale_jobs": stale, "processes_known": procs is not None,
            "recent": read_history(home), "now": _iso(now)}


__all__ = ["ActivityEmitter", "EngineProcess", "activity_dir", "append_history", "guess_words", "history_path",
           "job_counts", "list_processes", "parse_etime", "parse_ps", "pause_ownerless", "read_history",
           "read_progress_files", "run_state", "running_jobs", "snapshot"]
