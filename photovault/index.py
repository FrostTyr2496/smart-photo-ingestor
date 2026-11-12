"""Index a volume (idx-1): what is on a drive, by stat, partial hash and metadata. Read-only on the volume.

Nothing here writes to the volume being indexed (vault-rules 1, 2): it walks, stats and reads bytes for the
partial hash and metadata. `.lrcat` files are of kind `ignored`, so a Lightroom catalog is never opened
(vault-rules 7). Identity comes later from the full hash (idx-2, vault-rules 5).
"""

from __future__ import annotations

import sqlite3
import time
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Callable

from photovault import metadata, volumes
from photovault.catalog import repo
from photovault.config import Config
from photovault.groups import build_groups
from photovault.hashing import partial_sha256, sha256_file
from photovault.naming import body_nickname
from photovault.protocol import Emitter, human_bytes
from photovault.scan import FileEntry, walk

META_KINDS = {"raw", "jpeg", "heic", "video", "derivative"}
BATCH = 500
MTIME_TOLERANCE_NS = 2_000_000_000  # exFAT has 2 s mtime resolution (vault-rules 8)


def registered_volume(conn: sqlite3.Connection, mount: Path) -> sqlite3.Row:
    """The catalog row for a mounted volume, matched by marker or DiskArbitration UUID (§7)."""
    p = volumes.probe(Path(mount))
    row = repo.find_volume(conn, marker_id=p.marker_id, da_volume_uuid=p.da_volume_uuid)
    if row is None:
        raise volumes.UnknownVolume(
            f'{mount} is not in the catalog. Assign it first: photovault volumes assign "{mount}" --role LEGACY'
        )
    with repo.transaction(conn):
        conn.execute("UPDATE volumes SET last_mount_path = ?, last_seen_at = ? WHERE volume_id = ?",
                     (str(mount), repo.utcnow(), row["volume_id"]))
    return row


def unchanged(inst: sqlite3.Row | None, e: FileEntry) -> bool:
    return (inst is not None and inst["state"] != "missing" and inst["size"] == e.size
            and abs(inst["mtime_ns"] - e.mtime_ns) <= MTIME_TOLERANCE_NS)


def _record_groups(conn: sqlite3.Connection, cfg: Config, entries: list[FileEntry], meta: dict[Path, dict],
                   job_id: int, next_seq: int) -> int:
    """Upsert bodies and capture groups for groups with a serial and shutter count; returns the next seq.

    Legacy groups keep their original stem as `canonical_stem` (no renaming). Each member is recorded as a
    `committed` job item (src_rel = its path, group_id, role) so the baseline hash (idx-2) can link the asset
    to its group without re-reading metadata.
    """
    items = []
    for g in build_groups(entries, meta):
        if not (g.model and g.serial and g.shutter_count is not None):
            continue
        body_id = repo.get_or_create_body(conn, g.model, g.serial, body_nickname(cfg, g.model))
        row = repo.find_group(conn, body_id, g.shutter_count)
        if row is not None:
            group_id = int(row["group_id"])
        else:
            group_id = repo.insert_group(
                conn, body_id=body_id, shutter_count=g.shutter_count,
                capture_time=g.capture_time.isoformat() if g.capture_time else None, capture_offset=g.offset,
                time_source=g.time_source, canonical_stem=g.original_stem, original_stem=g.original_stem,
                original_num=g.original_num, shoot_id=None,
            )
        for m in g.members:
            items.append({"seq": next_seq, "src_rel": m.entry.rel_path, "dst_rel": None, "group_id": group_id,
                          "role": m.role})
            next_seq += 1
    if items:
        repo.add_job_items(conn, job_id, items)
        conn.executemany("UPDATE job_items SET state = 'committed' WHERE job_id = ? AND seq = ?",
                         [(job_id, it["seq"]) for it in items])
    return next_seq


def index_volume(
    conn: sqlite3.Connection, cfg: Config, mount: Path, *, emit: Emitter, should_stop: Callable[[], bool] = lambda: False
) -> int:
    """Index a registered volume; returns the job id. A paused index job on the volume is resumed.

    New or changed files get `state=indexed` and a partial hash (a changed file also loses its asset link);
    unchanged files (same size, mtime within 2 s) are skipped; instances not found on a complete walk
    become `missing`. Commits every ~500 files, a directory at a time so RAW+JPEG pairs stay together.
    """
    mount = Path(mount)
    start = time.monotonic()
    vol = registered_volume(conn, mount)
    vid = vol["volume_id"]
    with repo.transaction(conn):
        job = repo.latest_job(conn, kind="index_volume", source_volume_id=vid, state="paused")
        if job is not None:
            job_id = int(job["job_id"])
            emit.emit("warning", code="resuming", message=f"resuming index job {job_id}")
        else:
            job_id = repo.create_job(conn, kind="index_volume", source_volume_id=vid, target_volume_id=None,
                                     params={"mount": str(mount)})
        repo.set_job_state(conn, job_id, "running")
    next_seq = (conn.execute("SELECT MAX(seq) FROM job_items WHERE job_id = ?", (job_id,)).fetchone()[0] or 0) + 1

    emit.emit("progress", phase="scan", current=0, total=0, message=f"walking {mount}")
    entries = list(walk(mount))
    existing = repo.volume_instances(conn, vid)
    seen = {repo.path_key(e.rel_path) for e in entries}
    todo = [e for e in entries if not unchanged(existing.get(repo.path_key(e.rel_path)), e)]
    by_dir: dict[str, list[FileEntry]] = defaultdict(list)
    for e in todo:
        by_dir[str(PurePosixPath(e.rel_path).parent)].append(e)
    emit.emit("progress", phase="index", current=0, total=len(todo),
              message=f"{len(entries)} files, {len(entries) - len(todo)} unchanged")

    done = errors = 0
    batch: list[FileEntry] = []

    def flush() -> None:
        nonlocal next_seq, errors
        if not batch:
            return
        media = [e for e in batch if e.kind in META_KINDS]
        meta = metadata.read_metadata([e.abs_path for e in media], workers=cfg.exiftool_workers).meta if media else {}
        rows = []
        for e in batch:
            try:
                rows.append((e, partial_sha256(e.abs_path)))
            except OSError as exc:
                errors += 1
                emit.emit("warning", code="read_error", message=f"{e.rel_path}: {exc}")
        with repo.transaction(conn):
            for e, partial in rows:
                old = existing.get(repo.path_key(e.rel_path))
                if old is not None:
                    repo.clear_instance_identity(conn, old["instance_id"])
                repo.upsert_instance(conn, volume_id=vid, rel_path=e.rel_path, size=e.size, mtime_ns=e.mtime_ns,
                                     kind=e.kind, state="indexed", partial_sha256=partial)
            next_seq = _record_groups(conn, cfg, [e for e, _ in rows], meta, job_id, next_seq)
        batch.clear()

    for d in sorted(by_dir):
        if should_stop():
            with repo.transaction(conn):
                repo.set_job_state(conn, job_id, "paused")
            emit.emit("warning", code="paused", message="stopped; run the same index command again to resume")
            return job_id
        batch.extend(by_dir[d])
        done += len(by_dir[d])
        if len(batch) >= BATCH:
            flush()
            emit.emit("progress", phase="index", current=done, total=len(todo), message="")
    flush()

    with repo.transaction(conn):
        gone = [r["instance_id"] for k, r in existing.items() if k not in seen and r["state"] != "missing"]
        missing = repo.mark_missing(conn, gone) if gone else 0
        repo.set_job_state(conn, job_id, "done")
    emit.emit("result", job_id=job_id, volume=vol["label"], files=len(entries), indexed=len(todo) - errors,
              unchanged=len(entries) - len(todo), missing=missing, errors=errors,
              elapsed_s=round(time.monotonic() - start, 1))
    return job_id


def throttle_delay(bytes_done: int, elapsed_s: float, max_mbps: float | None) -> float:
    """Seconds to sleep so the average rate stays at or below `max_mbps` (1 MB = 1,000,000 bytes)."""
    if not max_mbps or max_mbps <= 0:
        return 0.0
    return max(0.0, bytes_done / (max_mbps * 1_000_000) - elapsed_s)


def group_links(conn: sqlite3.Connection, volume_id: str) -> dict[str, tuple[int, str]]:
    """`rel_path_key → (group_id, role)` from the volume's index jobs; the newest job wins."""
    rows = conn.execute(
        """
        SELECT ji.src_rel, ji.group_id, ji.role FROM job_items ji JOIN jobs j ON j.job_id = ji.job_id
        WHERE j.kind = 'index_volume' AND j.source_volume_id = ? AND ji.group_id IS NOT NULL
        ORDER BY j.job_id, ji.seq
        """,
        (volume_id,),
    ).fetchall()
    return {repo.path_key(r["src_rel"]): (int(r["group_id"]), r["role"]) for r in rows}


def baseline_hash(
    conn: sqlite3.Connection, cfg: Config, mount: Path, *, emit: Emitter, max_mbps: float | None = None,
    should_stop: Callable[[], bool] = lambda: False,
) -> int:
    """Full-hash every indexed, unlinked instance on a volume; returns the job id. Read-only on the volume.

    Each file is hashed, its asset found or created, and the instance set `verified` in its own transaction,
    so a stop or crash loses at most one file of work. A paused hash job on the volume is resumed. A file
    whose size or mtime no longer matches the index is skipped with a warning (re-run `index` first).
    """
    mount = Path(mount)
    start = time.monotonic()
    vol = registered_volume(conn, mount)
    vid = vol["volume_id"]
    with repo.transaction(conn):
        job = repo.latest_job(conn, kind="baseline_hash", source_volume_id=vid, state="paused")
        if job is not None:
            job_id = int(job["job_id"])
            emit.emit("warning", code="resuming", message=f"resuming hash job {job_id}")
        else:
            job_id = repo.create_job(conn, kind="baseline_hash", source_volume_id=vid, target_volume_id=None,
                                     params={"mount": str(mount), "max_mbps": max_mbps})
        repo.set_job_state(conn, job_id, "running")

    todo = conn.execute(
        "SELECT * FROM instances WHERE volume_id = ? AND asset_id IS NULL AND state = 'indexed' ORDER BY rel_path",
        (vid,),
    ).fetchall()
    links = group_links(conn, vid)
    total_bytes = sum(r["size"] for r in todo)
    emit.emit("progress", phase="hash", current=0, total=len(todo),
              message=f"{len(todo)} files, {human_bytes(total_bytes)} to hash")
    hashed = stale = 0
    bytes_done = 0
    for n, inst in enumerate(todo, 1):
        if should_stop():
            with repo.transaction(conn):
                repo.set_job_state(conn, job_id, "paused")
            emit.emit("warning", code="paused", message="stopped; run the same hash command again to resume")
            return job_id
        path = mount / inst["rel_path"]
        try:
            st = path.stat()
            if st.st_size != inst["size"] or abs(st.st_mtime_ns - inst["mtime_ns"]) > MTIME_TOLERANCE_NS:
                raise ValueError("changed since it was indexed; run index again")
            sha = sha256_file(path)
        except (OSError, ValueError) as exc:
            stale += 1
            emit.emit("warning", code="skipped", message=f"{inst['rel_path']}: {exc}")
            continue
        group_id, role = links.get(inst["rel_path_key"], (None, None))
        with repo.transaction(conn):
            asset_id = repo.insert_asset(conn, sha256=sha, size=inst["size"], partial_sha256=inst["partial_sha256"],
                                         kind=inst["kind"], group_id=group_id, group_role=role)
            repo.upsert_instance(conn, volume_id=vid, rel_path=inst["rel_path"], size=inst["size"],
                                 mtime_ns=inst["mtime_ns"], kind=inst["kind"], state="verified", asset_id=asset_id,
                                 verified_sha256=sha)
        hashed += 1
        bytes_done += inst["size"]
        delay = throttle_delay(bytes_done, time.monotonic() - start, max_mbps)
        if delay > 0:
            time.sleep(delay)
        if n % 100 == 0:
            elapsed = time.monotonic() - start
            emit.emit("progress", phase="hash", current=n, total=len(todo),
                      message=f"{human_bytes(bytes_done)} hashed, {human_bytes(bytes_done / elapsed if elapsed else 0)}/s")

    with repo.transaction(conn):
        repo.set_job_state(conn, job_id, "done")
    elapsed = time.monotonic() - start
    emit.emit("result", job_id=job_id, volume=vol["label"], hashed=hashed, skipped=stale,
              bytes=bytes_done, size=human_bytes(bytes_done), elapsed_s=round(elapsed, 1))
    return job_id
