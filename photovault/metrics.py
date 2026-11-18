"""Lifetime I/O metrics per drive (met-1, D-054). Spec: docs/specs/metrics/tasks.md.

Metrics live in `<home>/metrics.sqlite`, never in the catalog: they are telemetry and may be deleted at any time.
The I/O primitives (`hashing.sha256_file`, `hashing.partial_sha256`, `copy_engine.copy_verified`,
`copy_engine._hash_uncached`) count through `measure(op, path)`, which does nothing unless a `Recorder` is
installed. The CLI installs one per command. Recording never raises and never slows a run: counts are buffered
and flushed at most every `FLUSH_S` seconds and at `close`.
"""

from __future__ import annotations

import errno as errno_mod
import os
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OPS = ("read_full", "read_partial", "read_verify", "write")
READ_OPS = ("read_full", "read_partial", "read_verify")
STREAM_READ_OPS = ("read_full", "read_verify")
"""Reads that stream whole files; read MB/s is computed over these (partial reads are seek-bound)."""

FLUSH_S = 5.0
VOLUME_RELOAD_S = 30.0
MAX_PENDING_INCIDENTS = 1000
PARTIAL_READ_BYTES = 192 * 1024
"""Bytes a partial hash reads: three 64 KiB windows, or the whole file when it is this size or smaller."""

STORE_NAME = "metrics.sqlite"
SCHEMA_VERSION = 1
SCHEMA = """
CREATE TABLE IF NOT EXISTS daily (day TEXT NOT NULL, volume_key TEXT NOT NULL, op TEXT NOT NULL,
  source TEXT NOT NULL DEFAULT 'live' CHECK (source IN ('live','backfill')),
  files INTEGER NOT NULL DEFAULT 0, bytes INTEGER NOT NULL DEFAULT 0, busy_s REAL NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (day, volume_key, op, source));
CREATE TABLE IF NOT EXISTS drives (volume_key TEXT PRIMARY KEY, label TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS incidents (incident_id INTEGER PRIMARY KEY, ts TEXT NOT NULL, volume_key TEXT NOT NULL,
  op TEXT NOT NULL, kind TEXT NOT NULL, errno INTEGER, path TEXT NOT NULL, message TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs (run_id INTEGER PRIMARY KEY, pid INTEGER NOT NULL, command TEXT NOT NULL,
  started_at TEXT NOT NULL, finished_at TEXT, exit_code INTEGER, files INTEGER NOT NULL DEFAULT 0,
  bytes_read INTEGER NOT NULL DEFAULT 0, bytes_written INTEGER NOT NULL DEFAULT 0,
  errors INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

LOCAL_KEY = "local"


def _utcnow() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local_day(utc_iso: str | None = None) -> str:
    """Local calendar date, now or of a catalog UTC timestamp (`YYYY-MM-DDTHH:MM:SSZ`)."""
    if utc_iso is None:
        return datetime.now().date().isoformat()
    ts = datetime.strptime(utc_iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=UTC)
    return ts.astimezone().date().isoformat()


def store_path(home: Path) -> Path:
    return Path(home) / STORE_NAME


def open_store(home: Path) -> sqlite3.Connection:
    """Open (creating if needed) `<home>/metrics.sqlite` with its schema."""
    path = store_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA journal_mode=WAL")
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError(f"metrics store {path} is version {version}, newer than this engine ({SCHEMA_VERSION})")
        if version < SCHEMA_VERSION:
            conn.executescript(f"BEGIN;\n{SCHEMA}\nPRAGMA user_version={SCHEMA_VERSION};\nCOMMIT;")
    except Exception:
        conn.close()
        raise
    return conn


# --------------------------------------------------------------------------------------------------- recording


class Measure:
    """Counts one primitive's bytes. `add(n, busy=s)` with an explicit busy time replaces the wall-clock span."""

    __slots__ = ("bytes", "busy", "error", "claim_errors")

    def __init__(self, claim_errors: bool = True) -> None:
        self.bytes = 0
        self.busy: float | None = None
        self.error: BaseException | None = None
        self.claim_errors = claim_errors

    def add(self, n: int, busy: float | None = None) -> None:
        self.bytes += n
        if busy is not None:
            self.busy = (self.busy or 0.0) + busy

    def fail(self, exc: BaseException) -> None:
        """Attribute an error to this measure (used when several measures share one block)."""
        if self.error is None:
            self.error = exc


class _NullMeasure(Measure):
    def add(self, n: int, busy: float | None = None) -> None:
        pass

    def fail(self, exc: BaseException) -> None:
        pass


_NULL = _NullMeasure()


class Recorder:
    """Process-wide, thread-safe, batched recorder. No public method raises."""

    def __init__(self, home: Path, catalog_path: Path | None = None, *, command: str = "",
                 volumes_root: Path = Path("/Volumes"), flush_s: float = FLUSH_S) -> None:
        self.home = Path(home)
        self.catalog_path = Path(catalog_path) if catalog_path else None
        self.command = command
        self.volumes_root = os.path.abspath(str(volumes_root))
        self.flush_s = flush_s
        self.pid = os.getpid()
        self.started_at = _utcnow()
        self._lock = threading.Lock()
        self._daily: dict[tuple[str, str, str], list[float]] = {}
        self._incidents: list[tuple[str, str, str, str, int | None, str, str]] = []
        self._labels: dict[str, str] = {}
        self._run = {"files": 0, "bytes_read": 0, "bytes_written": 0, "errors": 0}
        self._run_id: int | None = None
        self._last_flush = time.monotonic()
        self._volumes: list[tuple[str, str, str]] = []  # (mount path, volume_id, label), longest first
        self._volumes_loaded_at: float | None = None
        self._dir_cache: dict[str, tuple[str, str, float | None]] = {}  # dir -> (key, label, miss time)
        self.flush_failures = 0
        self.closed = False

    # -- drive keys

    def _load_volumes(self) -> None:
        self._volumes_loaded_at = time.monotonic()
        self._dir_cache.clear()
        if self.catalog_path is None or not self.catalog_path.exists():
            self._volumes = []
            return
        try:
            conn = sqlite3.connect(f"file:{self.catalog_path}?mode=ro", uri=True, timeout=2.0)
            try:
                rows = conn.execute("SELECT volume_id, label, last_mount_path FROM volumes "
                                    "WHERE last_mount_path IS NOT NULL AND last_mount_path != ''").fetchall()
            finally:
                conn.close()
        except sqlite3.Error:
            rows = []
        vols = [(os.path.abspath(r[2]), r[0], r[1]) for r in rows]
        self._volumes = sorted(vols, key=lambda v: len(v[0]), reverse=True)

    def _match(self, path: str) -> tuple[str, str, bool]:
        """(key, label, registered) for an absolute path."""
        for mount, vid, label in self._volumes:
            if path == mount or path.startswith(mount.rstrip("/") + "/"):
                return vid, label, True
        root = self.volumes_root.rstrip("/") + "/"
        if path.startswith(root):
            name = path[len(root):].split("/", 1)[0]
            if name:
                return f"path:{root}{name}", name, False
        return LOCAL_KEY, "Mac", False

    def resolve(self, path: Path | str) -> tuple[str, str]:
        """The drive key and label for a file path (see the spec's "Drive key")."""
        p = os.path.abspath(str(path))
        d = os.path.dirname(p)
        now = time.monotonic()
        hit = self._dir_cache.get(d)
        if hit is not None and (hit[2] is None or now - hit[2] < VOLUME_RELOAD_S):
            return hit[0], hit[1]
        if self._volumes_loaded_at is None:
            self._load_volumes()
        key, label, registered = self._match(p)
        if not registered and now - (self._volumes_loaded_at or 0) >= VOLUME_RELOAD_S:
            self._load_volumes()
            key, label, registered = self._match(p)
        self._dir_cache[d] = (key, label, None if registered else time.monotonic())
        return key, label

    # -- recording

    def record(self, op: str, path: Path | str, nbytes: int, busy_s: float, *, files: int = 1,
               error: BaseException | None = None) -> None:
        try:
            with self._lock:
                key, label = self.resolve(path)
                self._labels[key] = label
                row = self._daily.setdefault((_local_day(), key, op), [0, 0, 0.0, 0])
                row[0] += files
                row[1] += nbytes
                row[2] += busy_s
                if op in READ_OPS:
                    self._run["bytes_read"] += nbytes
                    self._run["files"] += files if op != "read_verify" else 0
                else:
                    self._run["bytes_written"] += nbytes
                if error is not None:
                    row[3] += 1
                    self._run["errors"] += 1
                    self._add_incident(key, op, error, str(path))
            self._maybe_flush()
        except Exception:
            pass

    def incident(self, op: str, path: Path | str, kind: str, message: str) -> None:
        """Record a non-OSError incident (e.g. `verify_mismatch`) and count it as an error."""
        try:
            with self._lock:
                key, label = self.resolve(path)
                self._labels[key] = label
                row = self._daily.setdefault((_local_day(), key, op), [0, 0, 0.0, 0])
                row[3] += 1
                self._run["errors"] += 1
                if len(self._incidents) < MAX_PENDING_INCIDENTS:
                    self._incidents.append((_utcnow(), key, op, kind, None, str(path), message))
            self._maybe_flush()
        except Exception:
            pass

    def _add_incident(self, key: str, op: str, exc: BaseException, path: str) -> None:
        code = getattr(exc, "errno", None)
        kind = errno_mod.errorcode.get(code, type(exc).__name__) if isinstance(code, int) else type(exc).__name__
        if len(self._incidents) < MAX_PENDING_INCIDENTS:
            self._incidents.append((_utcnow(), key, op, kind, code if isinstance(code, int) else None, path,
                                    str(exc)[:500]))

    def _maybe_flush(self) -> None:
        if time.monotonic() - self._last_flush >= self.flush_s:
            self.flush()

    # -- persistence

    def flush(self, *, exit_code: int | None = None, final: bool = False) -> bool:
        """Write buffered counts to the store. Returns False (and keeps the buffer) when the store fails."""
        with self._lock:
            self._last_flush = time.monotonic()
            daily, incidents, labels = dict(self._daily), list(self._incidents), dict(self._labels)
            run = dict(self._run)
            did_io = self._run_id is not None or bool(daily)
            if not did_io and not incidents:
                return True
            try:
                conn = open_store(self.home)
            except Exception:
                self.flush_failures += 1
                return False
            try:
                now = _utcnow()
                conn.execute("BEGIN IMMEDIATE")
                conn.executemany(
                    "INSERT INTO daily (day, volume_key, op, source, files, bytes, busy_s, errors) "
                    "VALUES (?, ?, ?, 'live', ?, ?, ?, ?) ON CONFLICT (day, volume_key, op, source) DO UPDATE SET "
                    "files = files + excluded.files, bytes = bytes + excluded.bytes, "
                    "busy_s = busy_s + excluded.busy_s, errors = errors + excluded.errors",
                    [(d, k, o, int(r[0]), int(r[1]), float(r[2]), int(r[3])) for (d, k, o), r in daily.items()])
                conn.executemany(
                    "INSERT INTO drives (volume_key, label, updated_at) VALUES (?, ?, ?) "
                    "ON CONFLICT (volume_key) DO UPDATE SET label = excluded.label, updated_at = excluded.updated_at",
                    [(k, v, now) for k, v in labels.items()])
                conn.executemany("INSERT INTO incidents (ts, volume_key, op, kind, errno, path, message) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?)", incidents)
                if daily:
                    conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('live_since', ?)", (self.started_at,))
                run_id = self._run_id
                if run_id is None:
                    run_id = conn.execute(
                        "INSERT INTO runs (pid, command, started_at) VALUES (?, ?, ?)",
                        (self.pid, self.command, self.started_at)).lastrowid
                conn.execute("UPDATE runs SET files = ?, bytes_read = ?, bytes_written = ?, errors = ?, "
                             "finished_at = ?, exit_code = ? WHERE run_id = ?",
                             (run["files"], run["bytes_read"], run["bytes_written"], run["errors"],
                              now if final else None, exit_code if final else None, run_id))
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
                self.flush_failures += 1
                return False
            finally:
                conn.close()
            self._run_id = run_id
            # Only what was written is removed; counts added by other threads meanwhile stay buffered.
            for k, r in daily.items():
                cur = self._daily.get(k)
                if cur is not None:
                    left = [cur[i] - r[i] for i in range(4)]
                    if any(left):
                        self._daily[k] = left
                    else:
                        del self._daily[k]
            del self._incidents[:len(incidents)]
            return True

    def close(self, exit_code: int | None = None) -> None:
        """Final flush; finishes the `runs` row when this command did I/O. Never raises."""
        if self.closed:
            return
        self.closed = True
        try:
            if self._run_id is not None or self._daily or self._incidents:
                self.flush(exit_code=exit_code, final=True)
        except Exception:
            pass


_recorder: Recorder | None = None


def install(recorder: Recorder | None) -> Recorder | None:
    """Make `recorder` the process-wide recorder (None removes it). Returns the previous one."""
    global _recorder
    previous, _recorder = _recorder, recorder
    return previous


def current() -> Recorder | None:
    return _recorder


@contextmanager
def measure(op: str, path: Path | str, *, claim_errors: bool = True) -> Iterator[Measure]:
    """Count one primitive call. Yields a `Measure`; the caller adds bytes as it reads or writes them.

    On success one file is counted. An `OSError` escaping the block is recorded as an error and an incident
    (unless `claim_errors=False`, when only an error passed to `Measure.fail` counts). Other exceptions
    (interrupts, verify mismatches) record the bytes moved and no file. Nothing here changes the exception.
    """
    rec = _recorder
    if rec is None:
        yield _NULL
        return
    m = Measure(claim_errors)
    t0 = time.monotonic()
    try:
        yield m
    except BaseException as exc:
        if m.error is None and claim_errors and isinstance(exc, OSError):
            m.error = exc
        _finish(rec, op, path, m, t0, files=0)
        raise
    else:
        _finish(rec, op, path, m, t0, files=0 if m.error is not None else 1)


def _finish(rec: Recorder, op: str, path: Path | str, m: Measure, t0: float, *, files: int) -> None:
    busy = m.busy if m.busy is not None else time.monotonic() - t0
    rec.record(op, path, m.bytes, busy, files=files, error=m.error)


def incident(op: str, path: Path | str, kind: str, message: str) -> None:
    """Record an incident on the installed recorder, if any."""
    rec = _recorder
    if rec is not None:
        rec.incident(op, path, kind, message)


# --------------------------------------------------------------------------------------------------- reporting


def _mbps(nbytes: int, busy: float) -> float | None:
    return round(nbytes / busy / 1e6, 1) if busy > 0 and nbytes > 0 else None


def report(home: Path, days: int = 0) -> dict[str, Any]:
    """Lifetime totals, one row per drive (with live MB/s), recent incidents and runs; `days` adds a per-day table.

    Does not create the store: with no store the report is empty.
    """
    out: dict[str, Any] = {"store": str(store_path(home)), "live_since": None,
                           "totals": {"files_read": 0, "bytes_read": 0, "files_written": 0, "bytes_written": 0,
                                      "errors": 0},
                           "drives": [], "incidents": [], "runs": []}
    if days:
        out["days"] = []
    if not store_path(home).exists():
        return out
    conn = open_store(home)
    try:
        meta = {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM meta")}
        out["live_since"] = meta.get("live_since")
        labels = {r["volume_key"]: r["label"] for r in conn.execute("SELECT volume_key, label FROM drives")}
        drives: dict[str, dict[str, Any]] = {}
        for r in conn.execute("SELECT volume_key, op, source, SUM(files) f, SUM(bytes) b, SUM(busy_s) s, "
                              "SUM(errors) e, MAX(day) last FROM daily GROUP BY volume_key, op, source"):
            d = drives.setdefault(r["volume_key"], {
                "volume_key": r["volume_key"], "label": labels.get(r["volume_key"], r["volume_key"]),
                "files_read": 0, "bytes_read": 0, "files_written": 0, "bytes_written": 0, "errors": 0,
                "_rb": 0, "_rs": 0.0, "_wb": 0, "_ws": 0.0, "last_day": None})
            reading = r["op"] in READ_OPS
            if r["op"] != "read_verify":
                d["files_read" if reading else "files_written"] += r["f"]
            d["bytes_read" if reading else "bytes_written"] += r["b"]
            d["errors"] += r["e"]
            d["last_day"] = max(filter(None, (d["last_day"], r["last"])))
            if r["source"] == "live" and r["op"] in STREAM_READ_OPS:
                d["_rb"] += r["b"]
                d["_rs"] += r["s"]
            elif r["source"] == "live" and r["op"] == "write":
                d["_wb"] += r["b"]
                d["_ws"] += r["s"]
        rows = []
        for d in drives.values():
            d["read_mbps"] = _mbps(d.pop("_rb"), d.pop("_rs"))
            d["write_mbps"] = _mbps(d.pop("_wb"), d.pop("_ws"))
            rows.append(d)
            for k in out["totals"]:
                out["totals"][k] += d[k]
        out["drives"] = sorted(rows, key=lambda d: (-(d["bytes_read"] + d["bytes_written"]), d["label"]))
        out["incidents"] = [dict(r) | {"label": labels.get(r["volume_key"], r["volume_key"])} for r in conn.execute(
            "SELECT ts, volume_key, op, kind, errno, path, message FROM incidents ORDER BY incident_id DESC LIMIT 20")]
        out["runs"] = [dict(r) for r in conn.execute(
            "SELECT run_id, pid, command, started_at, finished_at, exit_code, files, bytes_read, bytes_written, "
            "errors FROM runs ORDER BY run_id DESC LIMIT 10")]
        if days:
            cutoff = datetime.now().date().toordinal() - days + 1
            first = datetime.fromordinal(cutoff).date().isoformat()
            out["days"] = [dict(r) | {"label": labels.get(r["volume_key"], r["volume_key"])} for r in conn.execute(
                "SELECT day, volume_key, "
                "SUM(CASE WHEN op != 'write' THEN bytes ELSE 0 END) AS bytes_read, "
                "SUM(CASE WHEN op = 'write' THEN bytes ELSE 0 END) AS bytes_written, "
                "SUM(CASE WHEN op IN ('read_full', 'read_partial') THEN files ELSE 0 END) AS files_read, "
                "SUM(errors) AS errors FROM daily WHERE day >= ? GROUP BY day, volume_key "
                "ORDER BY day DESC, volume_key", (first,))]
    finally:
        conn.close()
    return out


# --------------------------------------------------------------------------------------------------- backfill


def backfill(home: Path, catalog_path: Path) -> dict[str, Any]:
    """Rebuild the `backfill` rows from the catalog for work before `live_since` (a lower bound; see the spec).

    Re-running replaces the backfill rows. The catalog is opened read-only.
    """
    conn = open_store(home)
    try:
        conn.execute("INSERT OR IGNORE INTO meta (key, value) VALUES ('live_since', ?)", (_utcnow(),))
        live_since = conn.execute("SELECT value FROM meta WHERE key = 'live_since'").fetchone()[0]
        cat = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True, timeout=5.0)
        cat.row_factory = sqlite3.Row
        try:
            labels = {r["volume_id"]: r["label"] for r in cat.execute("SELECT volume_id, label FROM volumes")}
            written = cat.execute(
                "SELECT j.target_volume_id AS vol, ji.updated_at AS ts, COALESCE(a.size, 0) AS size "
                "FROM job_items ji JOIN jobs j ON j.job_id = ji.job_id LEFT JOIN assets a ON a.asset_id = ji.asset_id "
                "WHERE j.kind = 'card_ingest' AND ji.state = 'committed' AND ji.updated_at < ?", (live_since,)).fetchall()
            full = cat.execute(
                "SELECT i.volume_id AS vol, i.verified_at AS ts, i.size AS size FROM instances i "
                "WHERE i.state = 'verified' AND i.verified_at IS NOT NULL AND i.verified_at < ? AND NOT EXISTS ("
                "  SELECT 1 FROM job_items ji JOIN jobs j ON j.job_id = ji.job_id WHERE j.kind = 'card_ingest' "
                "  AND ji.state = 'committed' AND j.target_volume_id = i.volume_id AND ji.dst_rel = i.rel_path)",
                (live_since,)).fetchall()
            partial = cat.execute(
                "SELECT volume_id AS vol, last_seen_at AS ts, size FROM instances "
                "WHERE partial_sha256 IS NOT NULL AND last_seen_at < ?", (live_since,)).fetchall()
            errors = cat.execute(
                "SELECT volume_id AS vol, last_seen_at AS ts FROM instances "
                "WHERE state = 'mismatch' AND asset_id IS NULL AND last_seen_at < ?", (live_since,)).fetchall()
        finally:
            cat.close()

        rows: dict[tuple[str, str, str], list[int]] = {}

        def add(r: sqlite3.Row, op: str, nbytes: int, files: int = 1, errs: int = 0) -> None:
            if not r["vol"] or not r["ts"]:
                return
            row = rows.setdefault((_local_day(r["ts"]), r["vol"], op), [0, 0, 0])
            row[0] += files
            row[1] += nbytes
            row[2] += errs

        for r in written:
            add(r, "write", r["size"])
        for r in full:
            add(r, "read_full", r["size"])
        for r in partial:
            add(r, "read_partial", min(r["size"], PARTIAL_READ_BYTES))
        for r in errors:
            add(r, "read_full", 0, files=0, errs=1)

        now = _utcnow()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM daily WHERE source = 'backfill'")
            conn.executemany("INSERT INTO daily (day, volume_key, op, source, files, bytes, busy_s, errors) "
                             "VALUES (?, ?, ?, 'backfill', ?, ?, 0, ?)",
                             [(d, k, o, v[0], v[1], v[2]) for (d, k, o), v in rows.items()])
            conn.executemany("INSERT INTO drives (volume_key, label, updated_at) VALUES (?, ?, ?) "
                             "ON CONFLICT (volume_key) DO UPDATE SET label = excluded.label, "
                             "updated_at = excluded.updated_at",
                             [(k, v, now) for k, v in labels.items() if any(key[1] == k for key in rows)])
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        return {"live_since": live_since, "rows": len(rows),
                "files_read": sum(v[0] for (_, _, o), v in rows.items() if o == "read_full"),
                "files_written": sum(v[0] for (_, _, o), v in rows.items() if o == "write"),
                "errors": sum(v[2] for v in rows.values())}
    finally:
        conn.close()
