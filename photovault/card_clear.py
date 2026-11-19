"""Safe card clear (cc-1, D-096): delete only what is provably safe on a camera card. The one exception to
vault-rules 4 (and to the read-only card of rule 1).

A file under a CARD's ``DCIM/`` is deletable only when ALL of this holds, checked just before each deletion:

- the card is a registered CARD volume and the file is under ``DCIM/`` (nothing else is ever touched);
- the file is green (D-089): verified on a MASTER, or on two or more different WORKING/MASTER volumes;
- re-read from the card, its SHA-256 equals the asset's SHA-256, and size and mtime match the catalog;
- the catalog still holds the qualifying verified copies (``verified_sha256`` equal to the asset's);
- one such copy on a connected drive is re-hashed from the drive and equals the asset's SHA-256.

A capture group (RAW + JPEG, a photo and its sidecars) is deleted whole or not at all. Any surprise stops the run.
A deleted card instance becomes ``missing``; every deletion is a ``job_items`` row of a ``card_clear`` job.
The engine never formats a card and never deletes anything on a non-CARD volume.
"""

from __future__ import annotations

import os
import sqlite3
from collections import Counter
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from photovault import cards, testcard, volumes
from photovault.catalog import repo
from photovault.config import Config
from photovault.drives import scan_mounts
from photovault.hashing import sha256_file
from photovault.protocol import Emitter

MTIME_TOLERANCE_NS = 2_000_000_000  # exFAT has 2 s mtime resolution (vault-rules 8)

REASONS = {
    "outside_dcim": "Outside the DCIM folder",
    "only_on_card": "Only on this card",
    "unchecked": "Not checked, or changed since the check",
    "needs_backup": "One copy only",
    "copy_not_connected": "No drive holding a copy is connected",
    "group_not_green": "Another file of the same frame is not safe",
}


class ClearRefused(ValueError):
    """The clear is refused before anything is deleted."""


class Surprise(Exception):
    """A check failed just before a deletion; the run stops."""


# --------------------------------------------------------------------------- planning


def _card_volume(conn: sqlite3.Connection, mount: Path) -> sqlite3.Row:
    p = volumes.probe(Path(mount))
    if not p.looks_like_card:
        raise ClearRefused(f"{mount} is not a camera card (no DCIM folder)")
    row = repo.find_volume(conn, marker_id=p.marker_id, da_volume_uuid=p.da_volume_uuid)
    if row is None:
        raise ClearRefused(f"{mount} is not known yet: run `cards check` on it first")
    if row["role"] != "CARD":
        raise ClearRefused(f"{mount} is registered as {row['role']}, not CARD; only cards are cleared")
    return row


def connected_volumes(conn: sqlite3.Connection, volumes_root: Path, emit: Emitter) -> dict[str, Path]:
    """``volume_id -> mount`` for every registered, non-CARD drive that is plugged in now."""
    out: dict[str, Path] = {}
    for d in scan_mounts(conn, Path(volumes_root), emit):
        if d.get("volume_id") and d.get("mounted") and d.get("mount") and d.get("role") != "CARD":
            out[d["volume_id"]] = Path(d["mount"])
    return out


def _under_dcim(rel: str) -> bool:
    parts = PurePosixPath(rel).parts
    return len(parts) > 1 and parts[0].upper() == "DCIM"


class _Groups:
    """Union-find over card files: the same folder and stem, or the same catalog capture group."""

    def __init__(self) -> None:
        self.parent: dict[str, str] = {}

    def find(self, x: str) -> str:
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        self.parent[self.find(a)] = self.find(b)


def _group_ids(conn: sqlite3.Connection, asset_ids: list[int]) -> dict[int, int | None]:
    out: dict[int, int | None] = {}
    for i in range(0, len(asset_ids), 500):
        chunk = asset_ids[i:i + 500]
        q = ",".join("?" * len(chunk))
        for r in conn.execute(f"SELECT asset_id, group_id FROM assets WHERE asset_id IN ({q})", chunk):
            out[r["asset_id"]] = r["group_id"]
    return out


def plan_clear(conn: sqlite3.Connection, cfg: Config, mount: Path, *, volumes_root: Path, emit: Emitter) -> dict:
    """What a clear would delete and what it keeps, by reason. Read-only (never writes the catalog or the card)."""
    card = _card_volume(conn, mount)
    connected = connected_volumes(conn, volumes_root, emit)
    files = cards.card_files(conn, card["volume_id"], mount=Path(mount))
    gid = _group_ids(conn, [f["asset_id"] for f in files if f["asset_id"] is not None])
    groups = _Groups()
    for f in files:
        rel = f["rel_path"]
        p = PurePosixPath(rel)
        key = f"{p.parent}/{p.stem.casefold()}"
        groups.union(f"p:{key}", f"f:{rel}")
        g = gid.get(f["asset_id"]) if f["asset_id"] is not None else None
        if g is not None:
            groups.union(f"g:{g}", f"f:{rel}")
    reason: dict[str, str | None] = {}
    for f in files:
        rel = f["rel_path"]
        if not _under_dcim(rel):
            reason[rel] = "outside_dcim"
        elif f["tier"] != "backed_up":
            reason[rel] = f["tier"]
        elif not any(c["role"] in cards.WRITABLE_ROLES and c["volume_id"] in connected for c in f["copies"]):
            reason[rel] = "copy_not_connected"
        else:
            reason[rel] = None
    blocked = {groups.find(f"f:{f['rel_path']}") for f in files if reason[f["rel_path"]] is not None}
    for f in files:
        rel = f["rel_path"]
        if reason[rel] is None and groups.find(f"f:{rel}") in blocked:
            reason[rel] = "group_not_green"
    deletable = [f for f in files if reason[f["rel_path"]] is None]
    kept = Counter(reason[f["rel_path"]] for f in files if reason[f["rel_path"]] is not None)
    order: dict[str, int] = {}
    for f in deletable:
        order.setdefault(groups.find(f"f:{f['rel_path']}"), len(order))
    deletable.sort(key=lambda f: (order[groups.find(f"f:{f['rel_path']}")], f["rel_path"]))
    copy_drives = sorted({c["label"] for f in deletable for c in f["copies"] if c["volume_id"] in connected})
    return {
        "volume_id": card["volume_id"], "label": card["label"], "mount": str(mount),
        "total": len(files), "deletable_files": len(deletable), "deletable_bytes": sum(f["size"] for f in deletable),
        "groups": len(order),
        "kept": {k: {"files": n, "text": REASONS[k]} for k, n in kept.items()},
        "kept_files": sum(kept.values()),
        "copy_drives": copy_drives,
        "connected": {v: str(m) for v, m in connected.items()},
        "_deletable": [{"rel_path": f["rel_path"], "size": f["size"], "asset_id": f["asset_id"],
                        "group": order[groups.find(f"f:{f['rel_path']}")]} for f in deletable],
    }


def public_plan(plan: dict) -> dict:
    """The plan without its internal file list and mount map (for events)."""
    return {k: v for k, v in plan.items() if not k.startswith("_") and k != "connected"}


# --------------------------------------------------------------------------- running


def _check_file(conn: sqlite3.Connection, card: sqlite3.Row, mount: Path, item: dict, connected: dict[str, Path]) -> None:
    """Every check for one file, just before its group is deleted. Raises `Surprise`."""
    rel = item["rel_path"]
    inst = repo.get_instance(conn, card["volume_id"], rel)
    if inst is None or inst["state"] == "missing" or inst["asset_id"] != item["asset_id"]:
        raise Surprise(f"{rel}: the catalog no longer matches the card")
    asset = conn.execute("SELECT sha256, size FROM assets WHERE asset_id = ?", (item["asset_id"],)).fetchone()
    path = mount / rel
    try:
        st = path.lstat()
    except OSError as exc:
        raise Surprise(f"{rel}: cannot read the card file ({exc})") from exc
    if not path.is_file() or path.is_symlink():
        raise Surprise(f"{rel}: not a regular file")
    if st.st_size != inst["size"] or st.st_size != asset["size"] or abs(st.st_mtime_ns - inst["mtime_ns"]) > MTIME_TOLERANCE_NS:
        raise Surprise(f"{rel}: the card file changed since it was checked")
    try:
        card_sha = sha256_file(path)
    except OSError as exc:
        raise Surprise(f"{rel}: cannot re-read the card file ({exc})") from exc
    if card_sha != asset["sha256"]:
        raise Surprise(f"{rel}: the card file's checksum no longer matches the catalog")
    hide, hide_p = testcard.sql_hide(conn, "i")  # D-111: test-card copies on hidden drives do not count
    copies = conn.execute(
        f"""
        SELECT i.volume_id, i.rel_path, v.role FROM instances i JOIN volumes v ON v.volume_id = i.volume_id
        WHERE i.asset_id = ? AND i.state = 'verified' AND i.verified_sha256 = ? AND v.role IN ('WORKING','MASTER')
          AND i.volume_id != ?{hide}
        """,
        (item["asset_id"], asset["sha256"], card["volume_id"], *hide_p),
    ).fetchall()
    vols = {c["volume_id"] for c in copies}
    if not (any(c["role"] == "MASTER" for c in copies) or len(vols) >= 2):
        raise Surprise(f"{rel}: the catalog no longer holds two verified copies or a backup copy")
    # One copy on a connected drive is re-read: a backup drive first, else the first drive by id (deterministic).
    for c in sorted(copies, key=lambda c: (c["role"] != "MASTER", c["volume_id"])):
        root = connected.get(c["volume_id"])
        if root is None:
            continue
        copy = root / c["rel_path"]
        try:
            ok = copy.is_file() and copy.stat().st_size == asset["size"] and sha256_file(copy) == asset["sha256"]
        except OSError:
            ok = False
        if not ok:
            raise Surprise(f"{rel}: the copy on {root.name} is missing or does not match; restore it first")
        return
    raise Surprise(f"{rel}: no drive holding a copy is connected")


def _delete(conn: sqlite3.Connection, card: sqlite3.Row, mount: Path, rel: str) -> None:
    """Unlink one card file. Hard guards: a CARD volume, under DCIM, a regular file inside the mount."""
    if repo.get_volume(conn, card["volume_id"])["role"] != "CARD":
        raise Surprise("refusing: not a CARD volume")
    path = mount / rel
    dcim = (mount / PurePosixPath(rel).parts[0]).resolve()
    if not _under_dcim(rel) or path.is_symlink() or not path.resolve().is_relative_to(dcim):
        raise Surprise(f"{rel}: refusing to delete outside DCIM")
    os.unlink(path)


def run_clear(conn: sqlite3.Connection, cfg: Config, mount: Path, *, confirm: int, volumes_root: Path, emit: Emitter,
              should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Delete the plan's files group by group. ``confirm`` must equal the planned file count. Returns the summary."""
    mount = Path(mount)
    plan = plan_clear(conn, cfg, mount, volumes_root=volumes_root, emit=emit)
    n = plan["deletable_files"]
    if confirm != n:
        raise ClearRefused(f"confirmation {confirm} does not match the {n} file(s) this clear would delete; "
                           "run a dry run and type that number")
    if n == 0:
        raise ClearRefused("nothing can be deleted: " + (
            "; ".join(f"{v['files']} kept ({v['text'].lower()})" for v in plan["kept"].values()) or "the card has no files"))
    card = repo.get_volume(conn, plan["volume_id"])
    connected = {v: Path(m) for v, m in plan["connected"].items()}
    items = plan["_deletable"]
    with repo.transaction(conn):
        job_id = repo.create_job(conn, kind="card_clear", source_volume_id=card["volume_id"], target_volume_id=None,
                                 params={"confirm": confirm, "files": n, "groups": plan["groups"]})
        repo.add_job_items(conn, job_id, [{"seq": i, "src_rel": it["rel_path"], "group_id": None}
                                          for i, it in enumerate(items, 1)])
        repo.set_job_state(conn, job_id, "running")
    seq_of = {it["rel_path"]: i for i, it in enumerate(items, 1)}
    by_group: dict[int, list[dict]] = {}
    for it in items:
        by_group.setdefault(it["group"], []).append(it)
    deleted = freed = 0
    stopped: str | None = None
    current: str | None = None
    try:
        for gi, (_, members) in enumerate(sorted(by_group.items()), 1):
            if should_stop():
                stopped = "stopped by user"
                break
            emit.emit("progress", phase="clear", current=deleted, total=n, message=f"group {gi}/{len(by_group)}")
            for it in members:
                current = it["rel_path"]
                _check_file(conn, card, mount, it, connected)
            for it in members:
                current = it["rel_path"]
                seq = seq_of[it["rel_path"]]
                with repo.transaction(conn):
                    repo.set_item_state(conn, job_id, seq, "copying")
                _delete(conn, card, mount, it["rel_path"])
                with repo.transaction(conn):
                    inst = repo.get_instance(conn, card["volume_id"], it["rel_path"])
                    if inst is not None:
                        repo.mark_missing(conn, [inst["instance_id"]])
                    repo.set_item_state(conn, job_id, seq, "committed", asset_id=it["asset_id"])
                deleted += 1
                freed += it["size"]
                emit.emit("item", state="committed", src=it["rel_path"], dst=None)
            current = None
    except (Surprise, OSError) as exc:
        stopped = str(exc)
        if current is not None:
            with repo.transaction(conn):
                repo.set_item_state(conn, job_id, seq_of[current], "failed", error=stopped)
        emit.emit("error", code="clear_stopped", message=stopped)
    with repo.transaction(conn):
        repo.set_job_state(conn, job_id, "failed" if stopped else "done")
    emit.emit("result", job_id=job_id, deleted=deleted, bytes_freed=freed, planned=n, groups=len(by_group),
              stopped=stopped)
    return {"job_id": job_id, "deleted": deleted, "bytes": freed, "stopped": stopped}


def cleared_at(conn: sqlite3.Connection, volume_id: str | None) -> str | None:
    """When PhotoVault last cleared this card (the newest finished ``card_clear`` job that deleted files)."""
    if not volume_id:
        return None
    row = conn.execute(
        "SELECT j.finished_at FROM jobs j WHERE j.kind = 'card_clear' AND j.state = 'done' AND j.source_volume_id = ? "
        "ORDER BY j.job_id DESC LIMIT 1", (volume_id,)).fetchone()
    return row["finished_at"] if row else None
