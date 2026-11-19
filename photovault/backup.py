"""Backup job (bk-3, D-050, D-101): copy files from one drive onto a WORKING or MASTER drive. Kind `replicate`.

Two kinds of source:
  * a **card** (D-050): assets of one card that have a verified copy on the source WORKING drive and none on the
    target. With `follow_job` the job trails an import: each item is copied once the ingest has committed it.
  * a **folder** (D-101): the media files of a folder on any drive, LEGACY included. Nothing on the source is
    written. Files not hashed yet are hashed during the copy (read once).

All bytes go through `copy_engine.copy_verified` (vault-rules 3); nothing is overwritten or deleted. The copy's
SHA-256 must equal the asset's catalog hash: a mismatch marks the source instance `mismatch` and fails the item.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from collections import Counter
from pathlib import Path, PurePosixPath
from typing import Callable

from photovault import cards, targets, volumes
from photovault.catalog import repo
from photovault.config import Config
from photovault.copy_engine import CopyError, SourceMismatch, _hash_uncached, copy_verified
from photovault.hashing import partial_sha256, sha256_file
from photovault.ingest import META_KINDS, PROGRESS_EVERY, JobSummary, _inside, _prepare_resume
from photovault.naming import check_unique, windows_safe
from photovault.protocol import Emitter, human_bytes
from photovault.scan import classify, walk

WRITABLE_FS = ("exfat", "apfs")      # D-101: NTFS written through a third-party driver is never a target
TARGET_ROLES = ("WORKING", "MASTER")
POLL_S = 2.0                          # how often a trailing job looks for newly committed import items
PLANNED_GRACE_S = 120.0               # an import still `planned` after this long is treated as not running
MTIME_TOLERANCE_NS = 2_000_000_000    # exFAT keeps mtimes to 2 s


class BackupRefused(ValueError):
    """The plan or run is refused for a reason the user can fix (wrong drive, busy target, bad source)."""


# --------------------------------------------------------------------------- drives


def target_volume(conn: sqlite3.Connection, target: Path) -> tuple[sqlite3.Row, str]:
    """The registered WORKING/MASTER volume mounted at `target`, and its file system name. Refuses a card, an
    unregistered or unmarked drive, a LEGACY drive and any file system that is not exFAT or APFS."""
    tp = volumes.probe(target)
    if tp.looks_like_card:
        raise BackupRefused(f"target {target} looks like a camera card; cards are never written to")
    if tp.marker_id is None:
        raise BackupRefused(f'target {target} has no PhotoVault marker. Assign it first: '
                            f'photovault volumes assign "{target}" --role MASTER')
    row = repo.find_volume(conn, marker_id=tp.marker_id)
    if row is None or row["role"] not in TARGET_ROLES:
        raise BackupRefused(f"target {target} is not a WORKING or MASTER drive in this catalog "
                            f"(role: {row['role'] if row else 'unknown'}); LEGACY and CARD drives are never written to")
    fs = (tp.fs_type or "").lower()
    if fs not in WRITABLE_FS:
        raise BackupRefused(f"target {target} is formatted {fs or 'unknown'}; only exFAT and APFS drives are written to "
                            "(an NTFS drive written through a third-party driver is not safe)")
    return row, fs


def _source_volume(conn: sqlite3.Connection, source: Path) -> sqlite3.Row:
    tp = volumes.probe(source)
    row = repo.find_volume(conn, marker_id=tp.marker_id, da_volume_uuid=tp.da_volume_uuid)
    if row is None:
        raise BackupRefused(f'{source} is not in the catalog. Assign it first: '
                            f'photovault volumes assign "{source}" --role LEGACY')
    conn.execute("UPDATE volumes SET last_seen_at = ?, last_mount_path = ? WHERE volume_id = ?",
                 (repo.utcnow(), str(source), row["volume_id"]))   # the caller's plan transaction commits it
    return row


def _refuse_if_busy(conn: sqlite3.Connection, target_id: str, *, except_job: int | None = None) -> None:
    """One writer per target: a running or planned copy job onto the same drive blocks another."""
    row = conn.execute(
        "SELECT job_id, kind, state FROM jobs WHERE target_volume_id = ? AND kind IN ('replicate', 'card_ingest') "
        "AND state IN ('running', 'planned') AND job_id != ? ORDER BY job_id LIMIT 1", (target_id, except_job or 0)
    ).fetchone()
    if row is not None:
        raise BackupRefused(f"job {row['job_id']} ({row['kind']}, {row['state']}) is already writing to this drive; "
                            "only one copy job writes to a drive at a time")


def find_folder_mount(conn: sqlite3.Connection, path: Path) -> tuple[Path, str]:
    """Split `<mount>/<folder>` into the drive's mount and the folder's path relative to it: the nearest parent
    that is a mount point or the last mount of a registered drive."""
    path = Path(os.path.abspath(path))
    known = {os.path.abspath(r["last_mount_path"]) for r in repo.list_volumes(conn) if r["last_mount_path"]}
    for p in [path, *path.parents]:
        if str(p) in known or os.path.ismount(p):
            rel = path.relative_to(p)
            return p, "" if str(rel) == "." else rel.as_posix()
    raise BackupRefused(f"{path} is not on a registered drive")


# --------------------------------------------------------------------------- plan


def _card_items(conn: sqlite3.Connection, card_id: str, source_id: str, target_id: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT a.asset_id, MIN(si.rel_path) AS rel_path, MAX(si.size) AS size, a.group_id, a.group_role
        FROM instances ci
        JOIN instances si ON si.asset_id = ci.asset_id AND si.volume_id = ? AND si.state = 'verified'
        JOIN assets a ON a.asset_id = ci.asset_id
        WHERE ci.volume_id = ? AND ci.state != 'missing' AND ci.asset_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM instances ti WHERE ti.asset_id = ci.asset_id AND ti.volume_id = ? AND ti.state = 'verified')
        GROUP BY a.asset_id ORDER BY rel_path
        """, (source_id, card_id, target_id)).fetchall()
    return [{"src_rel": r["rel_path"], "dst_rel": r["rel_path"], "group_id": r["group_id"], "role": r["group_role"],
             "size": int(r["size"] or 0)} for r in rows]


def _follow_items(conn: sqlite3.Connection, ingest: sqlite3.Row) -> list[dict]:
    rows = conn.execute(
        """
        SELECT it.dst_rel, it.group_id, it.role, COALESCE(ci.size, 0) AS size FROM job_items it
        LEFT JOIN instances ci ON ci.volume_id = ? AND ci.rel_path_key = LOWER(it.src_rel)
        WHERE it.job_id = ? AND it.dst_rel IS NOT NULL ORDER BY it.seq
        """, (ingest["source_volume_id"], ingest["job_id"])).fetchall()
    return [{"src_rel": r["dst_rel"], "dst_rel": r["dst_rel"], "group_id": r["group_id"], "role": r["role"],
             "size": int(r["size"])} for r in rows]


def _unique(dst: str, used: set[str]) -> str:
    n, out = 2, dst
    while repo.path_key(out) in used:
        p = PurePosixPath(dst)
        out = str(p.with_name(f"{p.stem}_{n}{p.suffix}"))
        n += 1
    used.add(repo.path_key(out))
    return out


def _folder_items(conn: sqlite3.Connection, cfg: Config, source: Path, folder_rel: str, source_row: sqlite3.Row,
                  target_id: str) -> tuple[list[dict], int]:
    """Media files of `source/folder_rel` and their destinations under `Photography/Backups/<label>/<folder>`.
    Also returns how many are already verified on the target (known only for hashed files)."""
    root = source / folder_rel if folder_rel else source
    if not root.is_dir():
        raise BackupRefused(f"{root} is not a folder")
    base = PurePosixPath(cfg.ingest.working_subdir).parent / "Backups" / windows_safe(source_row["label"])
    for part in PurePosixPath(folder_rel).parts:
        base = base / windows_safe(part)
    used: set[str] = set()
    items, there = [], 0
    for e in walk(root):
        if e.kind not in META_KINDS:
            continue
        src_rel = str(PurePosixPath(folder_rel) / e.rel_path) if folder_rel else e.rel_path
        dst = base
        for part in PurePosixPath(e.rel_path).parts:
            dst = dst / windows_safe(part)
        inst = repo.get_instance(conn, source_row["volume_id"], src_rel)
        if (inst is not None and inst["asset_id"] is not None and inst["size"] == e.size and conn.execute(
                "SELECT 1 FROM instances WHERE asset_id = ? AND volume_id = ? AND state = 'verified'",
                (inst["asset_id"], target_id)).fetchone()):
            there += 1
        items.append({"src_rel": src_rel, "dst_rel": _unique(str(dst), used), "group_id": None, "role": None,
                      "size": e.size})
    return items, there


def plan_backup(
    conn: sqlite3.Connection, cfg: Config, *, source: Path, target: Path, card_volume_id: str | None = None,
    follow_job: int | None = None, source_folder: str | None = None, emit: Emitter,
) -> int:
    """Plan a backup onto `target` (a mounted WORKING/MASTER drive); returns the job id. Does not commit.

    Card source: `card_volume_id` and `source` (the mount of the drive that holds the card's files: WORKING or
    MASTER, or a read-only LEGACY drive, D-108); `follow_job` trails an import.
    Folder source: `source` is the drive's mount and `source_folder` the folder inside it (may be "").
    """
    source, target = Path(source), Path(target)
    folder_mode = source_folder is not None
    if folder_mode == (card_volume_id is not None):
        raise BackupRefused("give either a card (card_volume_id) or a folder (source_folder), not both or neither")
    if follow_job is not None and folder_mode:
        raise BackupRefused("--follow only applies to a card backup")
    if _inside(source, target):
        raise BackupRefused("source and target overlap; refusing")
    tvol, fs = target_volume(conn, target)
    svol = _source_volume(conn, source)
    if svol["volume_id"] == tvol["volume_id"]:
        raise BackupRefused("source and target are the same drive")
    if svol["role"] == "CARD":
        raise BackupRefused(f"{source} is a card; back a card up with --card from the drive its files were imported to")
    _refuse_if_busy(conn, tvol["volume_id"])

    already = 0
    if folder_mode:
        items, already = _folder_items(conn, cfg, source, source_folder or "", svol, tvol["volume_id"])
        mode = "folder"
    else:
        card = repo.get_volume(conn, card_volume_id)
        if card is None or card["role"] != "CARD":
            raise BackupRefused(f"{card_volume_id} is not a card in this catalog")
        if follow_job is not None:
            ingest = repo.get_job(conn, follow_job)
            if ingest is None or ingest["kind"] != "card_ingest":
                raise BackupRefused(f"job {follow_job} is not a card import")
            if ingest["source_volume_id"] != card_volume_id or ingest["target_volume_id"] != svol["volume_id"]:
                raise BackupRefused(f"job {follow_job} does not import this card onto {source}")
            items = _follow_items(conn, ingest)
        else:
            items = _card_items(conn, card_volume_id, svol["volume_id"], tvol["volume_id"])
        mode = "card"

    check_unique(it["dst_rel"] for it in items)
    params = {"mode": mode, "source_mount": str(source), "target_mount": str(target), "target_fs": fs,
              "card_volume_id": card_volume_id, "follow_job": follow_job, "folder_rel": source_folder,
              "follow_items": len(items) if follow_job is not None else None}
    job_id = repo.create_job(conn, kind="replicate", source_volume_id=svol["volume_id"],
                             target_volume_id=tvol["volume_id"], params=params)
    repo.add_job_items(conn, job_id, [{**{k: it[k] for k in ("src_rel", "dst_rel", "group_id", "role")}, "seq": n}
                                      for n, it in enumerate(items, 1)])
    planned_bytes = sum(it["size"] for it in items)
    free = None
    try:
        st = os.statvfs(target)
        free = st.f_bavail * st.f_frsize
    except OSError:
        pass
    if free is not None and planned_bytes > free:
        emit.emit("warning", code="no_space",
                  message=f"needs {human_bytes(planned_bytes)}, {tvol['label']} has {human_bytes(free)} free")
    if not items:
        emit.emit("warning", code="nothing_to_copy", message="no files to copy yet" if card_volume_id and not follow_job
                  else "no files to copy")
    folders = Counter(str(PurePosixPath(it["dst_rel"]).parent) for it in items)
    emit.emit(
        "result", job_id=job_id, mode=mode, files=len(items), already_there=already,
        planned_bytes=planned_bytes, planned=human_bytes(planned_bytes),
        target=tvol["label"], target_role=tvol["role"], target_fs=fs,
        target_free=human_bytes(free) if free is not None else "unknown", target_free_bytes=free,
        trailing=follow_job is not None,
        folders=[{"folder": f, "files": n} for f, n in sorted(folders.items())],
        sample=[f"{it['src_rel']} -> {it['dst_rel']}" for it in items[:5]],
    )
    return job_id


# --------------------------------------------------------------------------- make green (bk-7, D-108)


def _vol_info(vol: sqlite3.Row, mounted: dict) -> dict:
    return {"volume_id": vol["volume_id"], "label": vol["label"], "role": vol["role"],
            "mount": mounted.get(vol["volume_id"]), "connected": bool(mounted.get(vol["volume_id"]))}


def _list_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def plan_make_green(conn: sqlite3.Connection, cfg: Config | None, *, card_volume_id: str, mounted: dict,
                    home: Path | None, run=None) -> dict:
    """Read-only plan to turn a card's yellow files green (bk-7, D-108): each yellow file has one verified copy
    (a WORKING drive, or LEGACY drives only). Files are grouped by the connected drive that holds their copy, and
    each group gets the best connected WORKING/MASTER exFAT/APFS drive that holds none of them and has room
    (`targets.recommend`). A group with no possible copy says why. Nothing is written."""
    card = repo.get_volume(conn, card_volume_id)
    if card is None or card["role"] != "CARD":
        raise BackupRefused(f"{card_volume_id} is not a card in this catalog")
    mounted = {k: v for k, v in mounted.items() if v}
    vols = {v["volume_id"]: v for v in repo.list_volumes(conn)}
    yellow = [f for f in cards.card_files(conn, card_volume_id) if f["tier"] == "needs_backup"]
    groups: dict[str, dict] = {}
    for f in yellow:
        holders = sorted({c["volume_id"] for c in f["copies"]})
        live = sorted((h for h in holders if h in mounted),
                      key=lambda h: (vols[h]["role"] not in TARGET_ROLES, vols[h]["label"].lower()))
        key = live[0] if live else "offline:" + ",".join(holders)
        g = groups.setdefault(key, {"source_id": live[0] if live else None, "holders": set(), "files": 0, "bytes": 0})
        g["holders"].update(holders)
        g["files"] += 1
        g["bytes"] += f["size"]
    assigned: dict[str, int] = {}
    sources = []
    for g in sorted(groups.values(), key=lambda g: (g["source_id"] is None, -g["bytes"])):
        names = sorted(vols[h]["label"] for h in g["holders"])
        row = {"files": g["files"], "bytes": g["bytes"], "target": None, "ok": False, "reason": None}
        if g["source_id"] is None:
            row["source"] = {"volume_id": None, "label": _list_names(names), "role": None, "mount": None,
                             "connected": False}
            row["reason"] = (f"the only copy is on {names[0]}, which isn't connected" if len(names) == 1 else
                             f"the copies are on {_list_names(names)}, none of which is connected")
            sources.append(row)
            continue
        row["source"] = _vol_info(vols[g["source_id"]], mounted)
        kw = {"run": run} if run is not None else {}
        rec = targets.recommend(conn, home, mounted, planned_bytes=g["bytes"], source_volume_id=card_volume_id,
                                exclude=set(g["holders"]), **kw)
        pick = None
        for c in rec["candidates"]:
            if c["usable"] and (c["free_bytes"] is None or
                                c["free_bytes"] >= (assigned.get(c["volume_id"], 0) + g["bytes"]) * targets.HEADROOM):
                pick = c
                break
        if pick is None:
            if not rec["candidates"]:
                row["reason"] = "Plug in a second working drive"
            else:
                c = rec["candidates"][0]
                row["reason"] = (f"{c['label']} is full: {c['reasons'][0]}" if c["fs"] in WRITABLE_FS
                                 else f"{c['label']}: {c['reasons'][0]}")
        else:
            assigned[pick["volume_id"]] = assigned.get(pick["volume_id"], 0) + g["bytes"]
            row["ok"] = True
            row["target"] = {k: pick[k] for k in ("volume_id", "label", "role", "mount", "fs", "free_bytes", "reasons")}
        sources.append(row)
    possible = any(r["ok"] for r in sources)
    reasons = list(dict.fromkeys(r["reason"] for r in sources if r["reason"]))
    return {"card_volume_id": card_volume_id, "label": card["label"], "files": len(yellow),
            "bytes": sum(f["size"] for f in yellow), "possible": possible, "sources": sources,
            "reason": None if possible else (
                "Nothing to copy: no file on this card has just one copy" if not yellow else "; ".join(reasons))}


def run_make_green(conn: sqlite3.Connection, cfg: Config, plan: dict, emit: Emitter,
                   should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Run a make-green plan: one card-mode `replicate` job per possible source, one after another (one writer per
    target). Returns ``{"copied", "failed", "paused", "jobs"}``. A refused or failed job does not stop the others."""
    out = {"copied": 0, "failed": 0, "paused": False, "jobs": []}
    for row in plan["sources"]:
        if not row["ok"]:
            emit.emit("warning", code="make_green_skipped",
                      message=f"{row['source']['label']}: {row['files']} file(s) not copied: {row['reason']}")
            continue
        src, dst = Path(row["source"]["mount"]), Path(row["target"]["mount"])
        emit.emit("progress", phase="make_green", message=f"copying {row['files']} file(s) from "
                  f"{row['source']['label']} to {row['target']['label']}")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                job_id = plan_backup(conn, cfg, source=src, target=dst, card_volume_id=plan["card_volume_id"], emit=emit)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
            summary = run_backup(conn, cfg, job_id, emit, should_stop)
        except BackupRefused as exc:
            emit.emit("error", code="backup_refused", message=str(exc))
            out["failed"] += row["files"]
            continue
        out["jobs"].append(job_id)
        out["copied"] += summary.copied
        out["failed"] += summary.failed
        if summary.paused:
            out["paused"] = True
            break
    return out


# --------------------------------------------------------------------------- run


def _upstream_state(conn: sqlite3.Connection, ingest_id: int, dst_rel: str) -> str | None:
    r = conn.execute("SELECT state FROM job_items WHERE job_id = ? AND dst_rel = ? ORDER BY seq LIMIT 1",
                     (ingest_id, dst_rel)).fetchone()
    return r["state"] if r else None


def run_backup(
    conn: sqlite3.Connection, cfg: Config, job_id: int, emit: Emitter, should_stop: Callable[[], bool] = lambda: False,
    *, poll_s: float = POLL_S, sleep: Callable[[float], None] = time.sleep, planned_grace_s: float = PLANNED_GRACE_S,
) -> JobSummary:
    """Execute (or resume) a planned `replicate` job. Each item commits in its own transaction."""
    start = time.monotonic()
    job = repo.get_job(conn, job_id)
    if job is None or job["kind"] != "replicate":
        raise ValueError(f"no backup job {job_id}")
    if job["state"] == "done":
        raise ValueError(f"job {job_id} is already done")
    params = json.loads(job["params_json"])
    source, target = Path(params["source_mount"]), Path(params["target_mount"])
    folder_mode = params.get("mode") == "folder"
    follow = params.get("follow_job")
    svol = repo.get_volume(conn, job["source_volume_id"])
    tvol = repo.get_volume(conn, job["target_volume_id"])
    if tvol is None or tvol["role"] not in TARGET_ROLES or svol is None or svol["volume_id"] == tvol["volume_id"]:
        raise BackupRefused("refusing: the target must be a registered WORKING or MASTER drive other than the source")
    _refuse_if_busy(conn, tvol["volume_id"], except_job=job_id)

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
    with repo.transaction(conn):
        repo.set_job_state(conn, job_id, "running")
    total = len(repo.pending_items(conn, job_id))
    done_n = 0

    def pause(reason: str, code: str = "paused") -> JobSummary:
        with repo.transaction(conn):
            repo.set_job_state(conn, job_id, "paused")
        emit.emit("warning", code=code, message=f"{reason}; resume with: photovault resume {job_id}")
        return JobSummary(copied, skipped, failed, bytes_copied, time.monotonic() - start, True)

    def fail_item(seq: int, error: str) -> None:
        with repo.transaction(conn):
            repo.set_item_state(conn, job_id, seq, "failed", error=error)

    current: int | None = None
    waited = 0.0
    n_follow = params.get("follow_items")      # items past this seq are the tail (D-108): never looked up upstream
    if follow and n_follow is None:
        n_follow = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM job_items WHERE job_id = ?", (job_id,)).fetchone()[0]
    tail_planned = False
    try:
        while True:
            ready, waiting = [], []
            for it in repo.pending_items(conn, job_id):
                up = (_upstream_state(conn, follow, it["src_rel"]) if follow and it["seq"] <= n_follow
                      else "committed")
                if up == "committed":
                    ready.append(it)
                elif up == "skipped_dup":
                    # The import found the file already safe elsewhere and wrote nothing, so there is nothing to copy.
                    with repo.transaction(conn):
                        repo.set_item_state(conn, job_id, it["seq"], "skipped_dup")
                    skipped += 1
                    emit.emit("item", job_id=job_id, seq=it["seq"], state="skipped_dup", src=it["src_rel"],
                              dst=it["dst_rel"])
                elif up == "failed":
                    msg = "the import of this file failed, so there is nothing to copy"
                    fail_item(it["seq"], msg)
                    failed += 1
                    emit.emit("item", job_id=job_id, seq=it["seq"], state="failed", src=it["src_rel"],
                              dst=it["dst_rel"], error=msg)
                else:
                    waiting.append(it)
            if not ready:
                if not waiting:
                    if follow and not tail_planned:
                        # D-108: files the import skipped as already on the source, but missing on this target
                        tail_planned = True
                        added = _plan_tail(conn, job, params, n_follow)
                        if added:
                            total += added
                            continue
                    break
                ing = repo.get_job(conn, follow)
                if ing["state"] in ("paused", "failed", "done") or (ing["state"] == "planned" and waited >= planned_grace_s):
                    return pause(f"the import (job {follow}) is {ing['state']} and {len(waiting)} file(s) are not "
                                 "copied yet", "upstream_paused")
                if should_stop():
                    return pause("stopped by user")
                sleep(poll_s)
                waited += poll_s
                continue
            waited = 0.0
            for item in ready:
                if should_stop():
                    return pause("stopped by user")
                if not target_ok():
                    return pause(f"target {target} is missing or its marker changed")
                if not source.is_dir():
                    return pause(f"source {source} is not mounted")
                seq, src_rel, dst_rel = item["seq"], item["src_rel"], item["dst_rel"]
                with repo.transaction(conn):
                    repo.set_item_state(conn, job_id, seq, "copying")
                current = seq
                state, error = "failed", None
                try:
                    state, nbytes, method = _copy_item(conn, job, item, source, target, folder_mode)
                    if state == "committed":
                        copied += 1
                        bytes_copied += nbytes
                        sync_methods[method] += 1
                    else:
                        skipped += 1
                except SourceMismatch as exc:
                    error = str(exc)
                    with repo.transaction(conn):
                        conn.execute("UPDATE instances SET state = 'mismatch', last_seen_at = ? WHERE volume_id = ? "
                                     "AND rel_path_key = ?", (repo.utcnow(), svol["volume_id"], repo.path_key(src_rel)))
                    emit.emit("warning", code="source_mismatch",
                              message=f"{src_rel} on {svol['label']} no longer matches its recorded checksum; "
                                      "marked mismatch and not copied")
                    fail_item(seq, error)
                    failed += 1
                except (CopyError, FileExistsError, OSError) as exc:
                    if not target_ok():
                        with repo.transaction(conn):
                            repo.set_item_state(conn, job_id, seq, "planned")
                        return pause(f"target {target} went away ({exc})", "drive_gone")
                    if not source.is_dir():
                        with repo.transaction(conn):
                            repo.set_item_state(conn, job_id, seq, "planned")
                        return pause(f"source {source} went away ({exc})", "drive_gone")
                    error = str(exc)
                    fail_item(seq, error)
                    failed += 1
                current = None
                done_n += 1
                emit.emit("item", job_id=job_id, seq=seq, state=state, src=src_rel, dst=dst_rel,
                          **({"error": error} if error else {}))
                if done_n % PROGRESS_EVERY == 0 or done_n == total:
                    elapsed = time.monotonic() - start
                    rate = bytes_copied / elapsed if elapsed > 0 else 0
                    emit.emit("progress", phase="copy", current=done_n, total=total,
                              message=f"{human_bytes(bytes_copied)} copied, {human_bytes(rate)}/s, {failed} failed")
    except BaseException:
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
    emit.emit("result", job_id=job_id, copied=copied, skipped_dup=skipped, failed=failed, bytes_copied=bytes_copied,
              copied_size=human_bytes(bytes_copied), elapsed_s=round(elapsed, 1),
              sync_method=dict(sync_methods) or "none")
    return JobSummary(copied, skipped, failed, bytes_copied, elapsed, False)


def _plan_tail(conn: sqlite3.Connection, job: sqlite3.Row, params: dict, n_follow: int) -> int:
    """After a followed import has finished: add every asset of the card that is verified on the source and missing
    on the target (card-mode rules). Returns how many items were added."""
    have = {r[0] for r in conn.execute("SELECT src_rel FROM job_items WHERE job_id = ? AND seq > ?",
                                       (job["job_id"], n_follow))}
    items = [it for it in _card_items(conn, params["card_volume_id"], job["source_volume_id"], job["target_volume_id"])
             if it["src_rel"] not in have]
    if not items:
        return 0
    start = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM job_items WHERE job_id = ?", (job["job_id"],)).fetchone()[0]
    with repo.transaction(conn):
        repo.add_job_items(conn, job["job_id"], [{**{k: it[k] for k in ("src_rel", "dst_rel", "group_id", "role")},
                                                  "seq": start + n} for n, it in enumerate(items, 1)])
    return len(items)


def _copy_item(conn: sqlite3.Connection, job: sqlite3.Row, item: sqlite3.Row, source: Path, target: Path,
               folder_mode: bool) -> tuple[str, int, str]:
    """Copy one item and record it. Returns (state, bytes written, sync method). Raises on failure; the caller
    decides between a failed item and a pause."""
    job_id, seq, src_rel, dst_rel = job["job_id"], item["seq"], item["src_rel"], item["dst_rel"]
    sid, tid = job["source_volume_id"], job["target_volume_id"]
    src, dst = source / src_rel, target / dst_rel
    st = os.stat(src)
    inst = repo.get_instance(conn, sid, src_rel)
    kind = inst["kind"] if inst and inst["kind"] else classify(src.name)
    expected: str | None = None
    asset_id: int | None = inst["asset_id"] if inst else None
    if inst is not None and asset_id is not None and inst["state"] in ("verified", "mismatch"):
        fresh = inst["size"] == st.st_size and abs(inst["mtime_ns"] - st.st_mtime_ns) <= MTIME_TOLERANCE_NS
        if fresh or not folder_mode:      # a folder file edited since it was hashed is hashed again
            expected = conn.execute("SELECT sha256 FROM assets WHERE asset_id = ?", (asset_id,)).fetchone()[0]
    if not folder_mode and expected is None:
        raise CopyError(f"{src_rel} has no verified copy recorded on the source drive")

    # Already on the target (verified, at another path): nothing to write.
    if expected is not None and conn.execute(
            "SELECT 1 FROM instances WHERE asset_id = ? AND volume_id = ? AND state = 'verified' "
            "AND rel_path_key != ?", (asset_id, tid, repo.path_key(dst_rel))).fetchone():
        with repo.transaction(conn):
            repo.set_item_state(conn, job_id, seq, "skipped_dup", asset_id=asset_id)
        return "skipped_dup", 0, ""

    method, nbytes = "", 0
    partial = inst["partial_sha256"] if inst and inst["partial_sha256"] and expected else partial_sha256(src)
    if dst.exists():
        full = expected or sha256_file(src)
        if _hash_uncached(dst) != full:
            raise FileExistsError("name collision: a different file already exists at the destination")
        sha, size, dpartial, state = full, st.st_size, partial_sha256(dst), "skipped_dup"
    else:
        res = copy_verified(src, dst, mtime_ns=st.st_mtime_ns, expect_sha256=expected)
        sha, size, dpartial, state, method, nbytes = res.sha256, res.size, res.partial_sha256, "committed", \
            res.sync_method, res.size
    with repo.transaction(conn):
        if expected is None:
            # Hashed during the copy: the source instance gets its asset and `verified` state (baseline hash does the same).
            asset_id = repo.insert_asset(conn, sha256=sha, size=size, partial_sha256=dpartial, kind=kind,
                                         group_id=item["group_id"], group_role=item["role"])
            repo.upsert_instance(conn, volume_id=sid, rel_path=src_rel, size=st.st_size, mtime_ns=st.st_mtime_ns,
                                 kind=kind, state="verified", asset_id=asset_id, partial_sha256=partial,
                                 verified_sha256=sha)
        dstat = os.stat(dst)
        repo.upsert_instance(conn, volume_id=tid, rel_path=dst_rel, size=size, mtime_ns=dstat.st_mtime_ns, kind=kind,
                             state="verified", asset_id=asset_id, partial_sha256=dpartial, verified_sha256=sha)
        repo.set_item_state(conn, job_id, seq, state, asset_id=asset_id)
    return state, nbytes, method
