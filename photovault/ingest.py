"""Card ingest: plan (ing-6) and run/resume (ing-7). See system-design §4.

Sources are read-only (vault-rules 1). The only media writer is `copy_engine.copy_verified` (vault-rules 3).
Nothing here deletes anything (vault-rules 4). A file is skipped as a duplicate only when its full SHA-256
matches an asset with a `verified` instance on a non-CARD volume (vault-rules 5).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Callable

from photovault import metadata, volumes
from photovault.catalog import repo
from photovault.config import Config
from photovault.copy_engine import CopyError, _hash_uncached, copy_verified, sweep_partials
from photovault.groups import base_stem, build_groups
from photovault.index import group_links, unchanged
from photovault.hashing import partial_sha256, sha256_file
from photovault.naming import body_nickname, canonical_stem, check_unique, dest_dir, member_filename, shoot_date
from photovault.protocol import Emitter, human_bytes
from photovault.scan import walk

META_KINDS = {"raw", "jpeg", "heic", "video", "derivative"}
PROGRESS_EVERY = 25


@dataclass(frozen=True)
class JobSummary:
    copied: int
    skipped_dup: int
    failed: int
    bytes_copied: int
    elapsed_s: float
    paused: bool


def _inside(a: Path, b: Path) -> bool:
    a, b = a.resolve(), b.resolve()
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def _target_volume(conn: sqlite3.Connection, target: Path) -> sqlite3.Row:
    tp = volumes.probe(target)
    if tp.looks_like_card:
        raise ValueError(f"target {target} looks like a camera card; cards are never written to")
    if tp.marker_id is None:
        raise ValueError(
            f"target {target} has no PhotoVault marker. Assign it first: "
            f'photovault volumes assign "{target}" --role WORKING'
        )
    row = repo.find_volume(conn, marker_id=tp.marker_id)
    if row is None or row["role"] != "WORKING":
        raise ValueError(f"target {target} is not registered as WORKING in this catalog (role: {row and row['role']})")
    return row


def _batch(groups: list, limit: int | None) -> set[int]:
    """Indexes of the groups a ``--limit`` batch keeps: the first `limit` by capture time (D-090). Groups with no
    time sort last, by card path. Every group is kept when `limit` is None."""
    if limit is None:
        return set(range(len(groups)))
    if limit < 1:
        raise ValueError("limit must be at least 1")

    def key(n: int) -> tuple:
        g = groups[n]
        t = g.capture_time.replace(tzinfo=None).isoformat() if g.capture_time else ""
        return (g.capture_time is None, t, g.primary.entry.rel_path)

    return set(sorted(range(len(groups)), key=key)[:limit])


def _catalog_meta(conn: sqlite3.Connection, source_id: str, media: list) -> dict:
    """ing-14: the metadata `build_groups` needs, rebuilt from the card check's catalog rows (no ExifTool).

    A (folder, stem) bucket qualifies only when every media file in it has an unchanged instance (same size,
    mtime within 2 s) linked by an index job to one capture group that has a body, shutter count and capture
    time. Anything else is left for `read_metadata`, so mixed buckets are read whole.
    """
    existing = repo.volume_instances(conn, source_id)
    links = group_links(conn, source_id)
    buckets: dict[tuple[str, str], list] = {}
    for e in media:
        buckets.setdefault((str(PurePosixPath(e.rel_path).parent), base_stem(e).upper()), []).append(e)
    gids = sorted({g for g, _ in links.values()})
    rows: dict[int, sqlite3.Row] = {}
    for i in range(0, len(gids), 500):
        part = gids[i:i + 500]
        for r in conn.execute(
            "SELECT g.*, b.model AS model, b.serial AS serial FROM capture_groups g JOIN bodies b ON b.body_id = g.body_id "
            f"WHERE g.group_id IN ({','.join('?' * len(part))})", part,
        ).fetchall():
            rows[int(r["group_id"])] = r
    out: dict = {}
    for files in buckets.values():
        gid: set[int] = set()
        for e in files:
            key = repo.path_key(e.rel_path)
            link = links.get(key)
            if link is None or not unchanged(existing.get(key), e):
                gid = set()
                break
            gid.add(link[0])
        row = rows.get(next(iter(gid))) if len(gid) == 1 else None
        if row is None or not row["serial"] or row["shutter_count"] is None or not row["capture_time"]:
            continue
        try:
            when = datetime.fromisoformat(row["capture_time"])
        except ValueError:
            continue
        m: dict = {"Model": row["model"], "CameraSerialNumber": row["serial"], "ShutterCount": row["shutter_count"],
                   "SubSecTimeOriginal": row["subsec"], "OffsetTimeOriginal": row["capture_offset"]}
        if row["time_source"] != "mtime":  # build_groups falls back to the file's mtime by itself
            m.update(DateTimeOriginal=when, CaptureTimeSource=row["time_source"])
        for e in files:
            out[e.abs_path] = m
    return out


def plan_card_ingest(
    conn: sqlite3.Connection, cfg: Config, *, source: Path, target: Path, shoot_name: str | None, emit: Emitter,
    limit: int | None = None,
) -> int:
    """Plan an ingest of card `source` onto WORKING volume `target`; returns the job id. Does not commit.

    `limit` keeps only the first N capture groups by capture time (D-090). Names are still reserved for every
    group on the card, so a batch names its files exactly as a later full import of the same card would.
    """
    source, target = Path(source), Path(target)
    if _inside(source, target):
        raise ValueError("source and target overlap; refusing")
    sp = volumes.probe(source)
    if not sp.looks_like_card:
        raise ValueError(f"source {source} is not a camera card (no DCIM folder)")
    target_row = _target_volume(conn, target)
    source_id = volumes.register_volume(conn, sp)

    emit.emit("progress", phase="scan", current=0, total=0, message=f"scanning {source}")
    entries = list(walk(source))
    media = [e for e in entries if e.kind in META_KINDS]
    emit.emit("progress", phase="metadata", current=0, total=len(media), message=f"{len(entries)} files found")

    cat_meta = _catalog_meta(conn, source_id, media)
    todo = [e for e in media if e.abs_path not in cat_meta]
    if cat_meta:
        emit.emit("progress", phase="metadata", current=len(cat_meta), total=len(media),
                  message=f"metadata: {len(cat_meta):,} from the catalog, {len(todo):,} read from the card")
    if todo:
        base = len(cat_meta)

        def on_progress(done: int, total: int) -> None:
            emit.emit("progress", phase="metadata", current=base + done, total=len(media), message="reading metadata")

        result = metadata.read_metadata([e.abs_path for e in todo], workers=cfg.exiftool_workers, progress=on_progress)
    else:
        result = metadata.MetadataResult()
    result.meta.update(cat_meta)
    for path, err in sorted(result.errors.items()):
        emit.emit("warning", code="metadata", message=f"{path.relative_to(source)}: {err}")
    groups = build_groups(entries, result.meta)

    # Camera videos (Nikon MOV) often have no EXIF Model: inherit the most common body of the same card folder.
    folder_bodies: dict[str, Counter] = {}
    for g in groups:
        if g.model:
            folder_bodies.setdefault(str(PurePosixPath(g.primary.entry.rel_path).parent), Counter())[(g.model, g.serial)] += 1
    body_inferred = 0
    for g in groups:
        if not g.model:
            votes = folder_bodies.get(str(PurePosixPath(g.primary.entry.rel_path).parent))
            if votes:
                g.model, serial = votes.most_common(1)[0][0]
                g.serial = g.serial or serial
                body_inferred += 1

    # D-106: a frame is done when every member's card file is unchanged, hashed, and has a verified copy on the target.
    known = repo.volume_instances(conn, source_id)
    on_target = {int(r[0]) for r in conn.execute(
        "SELECT DISTINCT asset_id FROM instances WHERE volume_id = ? AND state = 'verified' AND asset_id IS NOT NULL",
        (target_row["volume_id"],))}
    done: set[int] = set()
    for gi, g in enumerate(groups):
        if all(unchanged(known.get(repo.path_key(m.entry.rel_path)), m.entry)
               and known[repo.path_key(m.entry.rel_path)]["asset_id"] in on_target for m in g.members):
            done.add(gi)
    done_files = sum(len(groups[gi].members) for gi in done)
    if limit is None:
        keep = set(range(len(groups)))  # done files become skipped_dup at run time (D-036)
    else:
        pool = [gi for gi in range(len(groups)) if gi not in done]
        keep = {pool[i] for i in _batch([groups[gi] for gi in pool], limit)}
    groups_total, files_total = len(groups), sum(len(g.members) for g in groups)
    items: list[dict] = []
    folders: Counter[str] = Counter()
    unknown_models: Counter[str] = Counter()
    mtime_dated = 0
    used: set[str] = set()
    for gi, group in enumerate(groups):
        if gi not in keep:
            _reserve_names(conn, cfg, group, shoot_name, used)
            continue
        nick = body_nickname(cfg, group.model)
        if group.model and group.model not in cfg.bodies:
            unknown_models[group.model] += 1
        body_id = repo.get_or_create_body(conn, group.model, group.serial, nick) if group.model else None
        if group.time_source == "mtime":
            mtime_dated += 1
        shoot = shoot_date(group.capture_time, cfg.naming.day_boundary_hour)
        folder = dest_dir(cfg, shoot, shoot_name, nick)
        existing = repo.find_group(conn, body_id, group.shutter_count)

        def new_shoot() -> int:
            return repo.get_or_create_shoot(
                conn, name=shoot_name or "", shoot_date=shoot.isoformat(), folder_rel=str(folder.parent)
            )

        if existing is not None and existing["shoot_id"] is not None:
            stem, group_id = existing["canonical_stem"], int(existing["group_id"])
        elif existing is not None:
            # A group made by `index` (legacy drive or card check) keeps the camera's stem and no shoot. Give it
            # the D-005 name now; its legacy files keep their on-disk names (ing-12, D-042).
            stem, group_id = canonical_stem(group, nick, cfg.naming.template), int(existing["group_id"])
            repo.promote_group(conn, group_id, canonical_stem=stem, shoot_id=new_shoot())
        else:
            stem = canonical_stem(group, nick, cfg.naming.template)
            shoot_id = new_shoot()
            group_id = repo.insert_group(
                conn,
                body_id=body_id,
                shutter_count=group.shutter_count,
                capture_time=group.capture_time.isoformat() if group.capture_time else None,
                capture_offset=group.offset,
                time_source=group.time_source,
                canonical_stem=stem,
                original_stem=group.original_stem,
                original_num=group.original_num,
                shoot_id=shoot_id,
                subsec=group.subsec,
            )
        for member in group.members:
            dst = _unique_dst(folder, member_filename(stem, member, group), used)
            folders[str(folder)] += 1
            items.append({"src_rel": member.entry.rel_path, "dst_rel": dst, "group_id": group_id,
                          "role": member.role, "entry": member.entry})
    check_unique(it["dst_rel"] for it in items)

    emit.emit("progress", phase="partial-hash", current=0, total=len(items), message="fingerprinting source files")
    for i, it in enumerate(items, 1):
        e = it["entry"]
        old = known.get(repo.path_key(e.rel_path))
        partial = old["partial_sha256"] if unchanged(old, e) and old["partial_sha256"] else partial_sha256(e.abs_path)
        repo.upsert_instance(conn, volume_id=source_id, rel_path=e.rel_path, size=e.size, mtime_ns=e.mtime_ns,
                             kind=e.kind, state="indexed", partial_sha256=partial)
        if i % 500 == 0:
            emit.emit("progress", phase="partial-hash", current=i, total=len(items), message="")

    params = {"shoot_name": shoot_name or "", "source_mount": str(source), "target_mount": str(target),
              "config_template": cfg.naming.template, "day_boundary_hour": cfg.naming.day_boundary_hour}
    job_id = repo.create_job(conn, kind="card_ingest", source_volume_id=source_id,
                             target_volume_id=target_row["volume_id"], params=params)
    repo.add_job_items(conn, job_id, [{**{k: v for k, v in it.items() if k != "entry"}, "seq": n}
                                      for n, it in enumerate(items, 1)])
    kinds = Counter(it["entry"].kind for it in items)
    planned_bytes = sum(it["entry"].size for it in items)
    free = None
    try:
        st = os.statvfs(target)
        free = st.f_bavail * st.f_frsize
    except OSError:
        pass
    if unknown_models:
        emit.emit("warning", code="unknown_body",
                  message="bodies not in [bodies] config (rented/borrowed?): "
                  + ", ".join(f"{m} ({n})" for m, n in unknown_models.items()))
    shoot_days = sorted({PurePosixPath(f).parent.name for f in folders})
    if shoot_name and len(shoot_days) > 1:
        emit.emit("warning", code="multi_day",
                  message=f"this card spans {len(shoot_days)} shoot days; the name {shoot_name!r} is applied to all of them")
    if free is not None and planned_bytes > free:
        emit.emit("warning", code="no_space", message=f"needs {human_bytes(planned_bytes)}, target has {human_bytes(free)} free")
    if limit is not None:
        emit.emit("warning", code="batch",
                  message=f"batch: {len(keep)} of {groups_total - len(done):,} frames still to copy "
                  f"({len(done):,} already on the target); run the import again for the rest")
    emit.emit(
        "result",
        job_id=job_id,
        files=len(items),
        groups=len(keep),
        limit=limit,
        groups_total=groups_total,
        files_total=files_total,
        already_on_target={"frames": len(done), "files": done_files},
        kinds=dict(kinds),
        planned_bytes=planned_bytes,
        planned=human_bytes(planned_bytes),
        target_free=human_bytes(free) if free is not None else "unknown",
        target_free_bytes=free,
        mtime_dated=mtime_dated,
        body_inferred=body_inferred,
        folders=[{"folder": f, "files": n} for f, n in sorted(folders.items())],
        sample=[f"{it['src_rel']} -> {it['dst_rel']}" for it in items[:5]],
        planned_items=[[it["src_rel"], it["dst_rel"], it["entry"].size] for it in items],  # im-7: every planned file
    )
    return job_id


def _unique_dst(folder: PurePosixPath, name: str, used: set[str]) -> str:
    """`folder/name`, with ``_2``, ``_3``… when two groups produce one name: keep both, never overwrite."""
    dst = str(folder / name)
    n = 2
    while repo.path_key(dst) in used:
        p = PurePosixPath(folder / name)
        dst = str(p.with_name(f"{p.stem}_{n}{p.suffix}"))
        n += 1
    used.add(repo.path_key(dst))
    return dst


def _reserve_names(conn: sqlite3.Connection, cfg: Config, group, shoot_name: str | None, used: set[str]) -> None:
    """Reserve the names a group outside the batch would get, without writing to the catalog (D-090)."""
    nick = body_nickname(cfg, group.model)
    body = conn.execute("SELECT body_id FROM bodies WHERE model = ? AND serial = ?",
                        (group.model, group.serial or "")).fetchone() if group.model else None
    existing = repo.find_group(conn, body["body_id"], group.shutter_count) if body is not None else None
    if existing is not None and existing["shoot_id"] is not None:
        stem = existing["canonical_stem"]
    else:
        stem = canonical_stem(group, nick, cfg.naming.template)
    folder = dest_dir(cfg, shoot_date(group.capture_time, cfg.naming.day_boundary_hour), shoot_name, nick)
    for member in group.members:
        _unique_dst(folder, member_filename(stem, member, group), used)


def _policy_met(cfg: Config, counts: dict[str, int]) -> bool:
    """Skip a file as a duplicate only when its existing verified copies already satisfy the policy (D-036).

    vault-rules 5 makes a verified non-CARD copy necessary; a copy on a LEGACY drive alone is not enough,
    or a card holding frames already on a legacy drive could never go green.
    """
    return bool(counts) and all(counts.get(role, 0) >= need for role, need in cfg.policy.required.items())


def _job_paths(job: sqlite3.Row) -> tuple[Path, Path]:
    params = json.loads(job["params_json"])
    return Path(params["source_mount"]), Path(params["target_mount"])


def _prepare_resume(conn: sqlite3.Connection, job: sqlite3.Row, target: Path, emit: Emitter) -> None:
    """Before (re)running a job: retry failed items, re-plan committed items whose file has vanished from the
    target, and sweep the engine's leftover `.pvpartial` files. Cheap: one stat per finished item."""
    job_id = job["job_id"]
    rows = conn.execute("SELECT * FROM job_items WHERE job_id = ? ORDER BY seq", (job_id,)).fetchall()
    retried = vanished = 0
    with repo.transaction(conn):
        for it in rows:
            if it["state"] == "failed":
                repo.set_item_state(conn, job_id, it["seq"], "planned")
                retried += 1
            elif it["state"] == "committed":
                inst = repo.get_instance(conn, job["target_volume_id"], it["dst_rel"])
                try:
                    ok = inst is not None and os.stat(target / it["dst_rel"]).st_size == inst["size"]
                except OSError:
                    ok = False
                if not ok:
                    if inst is not None:
                        conn.execute("UPDATE instances SET state = 'missing' WHERE instance_id = ?",
                                     (inst["instance_id"],))
                    repo.set_item_state(conn, job_id, it["seq"], "planned")
                    vanished += 1
    swept = sweep_partials([target / it["dst_rel"] for it in rows if it["dst_rel"]])
    if retried:
        emit.emit("warning", code="retry_failed", message=f"retrying {retried} failed item(s)")
    if vanished:
        emit.emit("warning", code="target_missing",
                  message=f"{vanished} committed file(s) are gone from {target}; marked missing and re-queued")
    if swept:
        emit.emit("warning", code="partials_swept", message=f"removed {swept} leftover .pvpartial file(s)")


def run_job(
    conn: sqlite3.Connection, cfg: Config, job_id: int, *, emit: Emitter, should_stop: Callable[[], bool] = lambda: False
) -> JobSummary:
    """Execute (or resume) a planned card-ingest job. Each item commits in its own transaction."""
    start = time.monotonic()
    job = repo.get_job(conn, job_id)
    if job is None:
        raise ValueError(f"no job {job_id}")
    if job["state"] == "done":
        raise ValueError(f"job {job_id} is already done")
    source, target = _job_paths(job)
    tvol = conn.execute("SELECT * FROM volumes WHERE volume_id = ?", (job["target_volume_id"],)).fetchone()
    if tvol is None or tvol["role"] == "CARD" or job["source_volume_id"] == job["target_volume_id"]:
        raise ValueError("refusing: the target must be a registered non-CARD volume different from the source")

    def target_ok() -> bool:
        try:
            return target.is_dir() and volumes.read_marker(target) == tvol["marker_id"]
        except (OSError, ValueError):
            return False

    copied = skipped = failed = 0
    bytes_copied = 0
    sync_methods: Counter[str] = Counter()
    if target_ok():
        _prepare_resume(conn, job, target, emit)
    items = repo.pending_items(conn, job_id)
    total = len(items)
    with repo.transaction(conn):
        repo.set_job_state(conn, job_id, "running")

    def pause(reason: str) -> JobSummary:
        with repo.transaction(conn):
            repo.set_job_state(conn, job_id, "paused")
        emit.emit("warning", code="paused", message=f"{reason}; resume with: photovault resume {job_id}")
        return JobSummary(copied, skipped, failed, bytes_copied, time.monotonic() - start, True)

    current: int | None = None
    try:
        for n, item in enumerate(items, 1):
            if should_stop():
                return pause("stopped by user")
            if not target_ok():
                return pause(f"target {target} is missing or its marker changed")
            if not source.is_dir():
                return pause(f"source {source} is not mounted")
            seq, src_rel, dst_rel = item["seq"], item["src_rel"], item["dst_rel"]
            src, dst = source / src_rel, target / dst_rel
            with repo.transaction(conn):
                repo.set_item_state(conn, job_id, seq, "copying")
            current = seq
            src_inst = repo.get_instance(conn, job["source_volume_id"], src_rel)
            kind = src_inst["kind"] if src_inst else "raw"
            state, error, asset_id = "failed", None, None
            try:
                st = os.stat(src)
                partial = (src_inst["partial_sha256"] if src_inst and src_inst["partial_sha256"] else partial_sha256(src))
                full = None
                dup = None
                for cand in repo.find_candidates(conn, st.st_size, partial):
                    full = full or sha256_file(src)
                    if cand["sha256"] == full and _policy_met(cfg, repo.verified_counts_by_role(conn, cand["asset_id"])):
                        dup = cand
                        break
                if dup is not None:
                    state, asset_id = "skipped_dup", int(dup["asset_id"])
                    with repo.transaction(conn):
                        repo.upsert_instance(conn, volume_id=job["source_volume_id"], rel_path=src_rel, size=st.st_size,
                                             mtime_ns=st.st_mtime_ns, kind=kind, state="indexed", asset_id=asset_id,
                                             partial_sha256=partial)
                        repo.set_item_state(conn, job_id, seq, state, asset_id=asset_id)
                    skipped += 1
                else:
                    if dst.exists():
                        full = full or sha256_file(src)
                        if _hash_uncached(dst) != full:
                            raise FileExistsError("name collision: a different file already exists at the destination")
                        sha, size, dpartial = full, st.st_size, partial_sha256(dst)
                    else:
                        res = copy_verified(src, dst, mtime_ns=st.st_mtime_ns)
                        if full is not None and res.sha256 != full:
                            raise CopyError("source changed during copy")
                        sha, size, dpartial = res.sha256, res.size, res.partial_sha256
                        sync_methods[res.sync_method] += 1
                        bytes_copied += size
                    with repo.transaction(conn):
                        asset_id = repo.insert_asset(conn, sha256=sha, size=size, partial_sha256=dpartial, kind=kind,
                                                     group_id=item["group_id"], group_role=item["role"])
                        dstat = os.stat(dst)
                        repo.upsert_instance(conn, volume_id=job["target_volume_id"], rel_path=dst_rel, size=size,
                                             mtime_ns=dstat.st_mtime_ns, kind=kind, state="verified", asset_id=asset_id,
                                             partial_sha256=dpartial, verified_sha256=sha)
                        repo.upsert_instance(conn, volume_id=job["source_volume_id"], rel_path=src_rel, size=st.st_size,
                                             mtime_ns=st.st_mtime_ns, kind=kind, state="indexed", asset_id=asset_id,
                                             partial_sha256=partial)
                        repo.set_item_state(conn, job_id, seq, "committed", asset_id=asset_id)
                    state = "committed"
                    copied += 1
            except (CopyError, FileExistsError, OSError) as exc:
                if not target_ok():
                    with repo.transaction(conn):
                        repo.set_item_state(conn, job_id, seq, "planned")
                    return pause(f"target {target} went away ({exc})")
                if not source.is_dir():
                    with repo.transaction(conn):
                        repo.set_item_state(conn, job_id, seq, "planned")
                    return pause(f"source {source} went away ({exc})")
                error = str(exc)
                with repo.transaction(conn):
                    repo.set_item_state(conn, job_id, seq, "failed", error=error)
                failed += 1
            current = None
            emit.emit("item", job_id=job_id, seq=seq, state=state, src=src_rel, dst=dst_rel, **({"error": error} if error else {}))
            if n % PROGRESS_EVERY == 0 or n == total:
                elapsed = time.monotonic() - start
                rate = bytes_copied / elapsed if elapsed > 0 else 0
                emit.emit("progress", phase="copy", current=n, total=total,
                          message=f"{human_bytes(bytes_copied)} copied, {human_bytes(rate)}/s, {failed} failed")
    except BaseException:
        # Unexpected error or a hard interrupt: leave a resumable state, never a half-recorded item.
        try:
            with repo.transaction(conn):
                if current is not None:
                    repo.set_item_state(conn, job_id, current, "planned")
                repo.set_job_state(conn, job_id, "paused")
        except sqlite3.Error:
            pass
        raise

    with repo.transaction(conn):
        repo.set_job_state(conn, job_id, "failed" if failed else "done")
    elapsed = time.monotonic() - start
    emit.emit("result", job_id=job_id, copied=copied, skipped_dup=skipped, failed=failed,
              bytes_copied=bytes_copied, copied_size=human_bytes(bytes_copied), elapsed_s=round(elapsed, 1),
              sync_method=dict(sync_methods) or "none")
    return JobSummary(copied, skipped, failed, bytes_copied, elapsed, False)
