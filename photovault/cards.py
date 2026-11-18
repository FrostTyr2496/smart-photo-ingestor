"""Cards: is each file on a camera card safe to remove? (spec `cards`, D-046, D-048). Read-only on every card.

A card file is ranked by where else its bytes live (identity is the full SHA-256, vault-rules 5; a copy on a
CARD never counts):

- ``backed_up`` (green): a verified copy on a MASTER (backup) drive.
- ``needs_backup`` (yellow): a verified copy on WORKING (the Mac folder included) or LEGACY, none on MASTER.
- ``only_on_card`` (red): no verified copy anywhere else, or the card could not read the file.
- ``unchecked`` (grey): not checked yet, or changed since, or its size and partial hash match a hashed file
  elsewhere but its own full hash is not known yet.

A card's, or a camera's, colour is its worst file (red > grey > yellow > green). This is separate from
``card-status`` (D-003), which is unchanged. ``check_card`` only reads the card: it is ``index`` + ``hash``.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from collections.abc import Callable, Iterable
from pathlib import Path, PurePosixPath

from photovault import volumes
from photovault.catalog import repo
from photovault.config import Config
from photovault.index import baseline_hash, index_volume
from photovault.protocol import Emitter
from photovault.scan import walk

TIERS = ("only_on_card", "unchecked", "needs_backup", "backed_up")  # worst first
RANK = {t: n for n, t in enumerate(TIERS)}
EMPTY = "empty"
UNKNOWN_CAMERA = "unknown"
MTIME_TOLERANCE_NS = 2_000_000_000  # exFAT has 2 s mtime resolution (vault-rules 8)


def worst(tiers: Iterable[str]) -> str:
    """The worst tier in `tiers`, or ``empty`` when there are none."""
    return min(tiers, key=RANK.__getitem__, default=EMPTY)


# --------------------------------------------------------------------------- check


def check_card(conn: sqlite3.Connection, cfg: Config, mount: Path, *, emit: Emitter,
               should_stop: Callable[[], bool] = lambda: False) -> dict:
    """Register the card (CARD) if new, then index and hash it. Reads the card only. Returns a summary dict with
    ``volume_id``, ``paused`` and the tier counts."""
    mount = Path(mount)
    p = volumes.probe(mount)
    if not p.looks_like_card:
        raise ValueError(f"{mount} is not a camera card (no DCIM folder)")
    with repo.transaction(conn):
        vid = volumes.register_volume(conn, p)
    if repo.get_volume(conn, vid)["role"] != "CARD":
        raise ValueError(f"{mount} is registered as {repo.get_volume(conn, vid)['role']}, not as a card")
    job = index_volume(conn, cfg, mount, emit=emit, should_stop=should_stop)
    paused = repo.get_job(conn, job)["state"] == "paused"
    if not paused:
        job = baseline_hash(conn, cfg, mount, emit=emit, should_stop=should_stop)
        paused = repo.get_job(conn, job)["state"] == "paused"
    summary = card_summary(conn, vid, mount=mount)
    return {"volume_id": vid, "paused": paused, "tier": summary["tier"], "counts": summary["counts"]}


# --------------------------------------------------------------------------- classify


def _catalog_rows(conn: sqlite3.Connection, volume_id: str) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        """
        SELECT i.rel_path, i.rel_path_key, i.size, i.mtime_ns, i.kind, i.state, i.asset_id,
          (SELECT GROUP_CONCAT(x.role || char(31) || x.label || char(31) || x.volume_id, char(30)) FROM (
             SELECT DISTINCT v.role, v.label, v.volume_id FROM instances o JOIN volumes v ON v.volume_id = o.volume_id
             WHERE o.asset_id = i.asset_id AND o.state = 'verified' AND v.role != 'CARD') x) AS copies,
          CASE WHEN i.asset_id IS NULL AND i.partial_sha256 IS NOT NULL THEN (
             SELECT MIN(v.label) FROM assets a JOIN instances o ON o.asset_id = a.asset_id
             JOIN volumes v ON v.volume_id = o.volume_id
             WHERE a.size = i.size AND a.partial_sha256 = i.partial_sha256 AND o.state = 'verified'
               AND v.role != 'CARD') END AS candidate
        FROM instances i WHERE i.volume_id = ? AND i.state != 'missing'
        """,
        (volume_id,),
    ).fetchall()
    return {r["rel_path_key"]: r for r in rows}


def _classify(row: sqlite3.Row) -> tuple[str, str, list[dict]]:
    """(tier, reason, copies) for one catalog row of a card file."""
    copies = []
    for part in (row["copies"] or "").split("\x1e"):
        if part:
            role, label, vid = part.split("\x1f", 2)
            copies.append({"role": role, "label": label, "volume_id": vid})
    copies.sort(key=lambda c: (c["role"] != "MASTER", c["role"] != "WORKING", c["label"]))
    if row["state"] == "mismatch":
        return "only_on_card", "The card could not read this file", copies
    if row["asset_id"] is None:
        if row["candidate"]:
            return "unchecked", f"Looks like a file on {row['candidate']}; check the card to confirm", copies
        return "only_on_card", "No other drive has this file", copies
    masters = [c["label"] for c in copies if c["role"] == "MASTER"]
    if masters:
        return "backed_up", f"On backup drive {_names(masters)}", copies
    if copies:
        return "needs_backup", f"Only on {_names([c['label'] for c in copies])}, not on a backup drive yet", copies
    return "only_on_card", "No other drive has this file", copies


def _names(labels: list[str]) -> str:
    """``A``, ``A and B``, ``A, B and C``."""
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} and {labels[-1]}"


def _cameras_by_file(conn: sqlite3.Connection, volume_id: str) -> dict[str, int | None]:
    """``rel_path_key → body_id`` from the card's index and ingest job items (newest job wins)."""
    rows = conn.execute(
        """
        SELECT ji.src_rel, g.body_id FROM job_items ji JOIN jobs j ON j.job_id = ji.job_id
        JOIN capture_groups g ON g.group_id = ji.group_id
        WHERE j.source_volume_id = ? AND ji.group_id IS NOT NULL
        ORDER BY j.job_id, ji.seq
        """,
        (volume_id,),
    ).fetchall()
    return {repo.path_key(r["src_rel"]): r["body_id"] for r in rows}


def camera_names(conn: sqlite3.Connection) -> dict[int, str]:
    """``body_id → name`` for people: the nickname (or model), numbered when two bodies share it. Never the serial."""
    rows = conn.execute("SELECT body_id, model, nickname FROM bodies ORDER BY body_id").fetchall()
    base = {r["body_id"]: (r["nickname"] or r["model"] or "Camera") for r in rows}
    seen: Counter[str] = Counter(base.values())
    n: Counter[str] = Counter()
    out = {}
    for bid, name in base.items():
        if seen[name] > 1:
            n[name] += 1
            name = f"{name} #{n[name]}"
        out[bid] = name
    return out


def card_files(conn: sqlite3.Connection, volume_id: str | None, *, mount: Path | None = None) -> list[dict]:
    """Every file on a card with its tier, most urgent first.

    With `mount` (the card is plugged in) the card is walked (names, sizes, mtimes; no reads) and a file the
    catalog does not know, or that changed since the check, is ``unchecked``. Without it, the catalog's last
    known state is used.
    """
    rows = _catalog_rows(conn, volume_id) if volume_id else {}
    bodies = _cameras_by_file(conn, volume_id) if volume_id else {}
    out: list[dict] = []
    if mount is not None:
        listing = [(e.rel_path, e.size, e.mtime_ns, e.kind) for e in walk(Path(mount))]
    else:
        listing = [(r["rel_path"], r["size"], r["mtime_ns"], r["kind"]) for r in rows.values()]
    for rel, size, mtime_ns, kind in listing:
        key = repo.path_key(rel)
        row = rows.get(key)
        if row is None:
            tier, reason, copies = "unchecked", "Not checked yet", []
        elif row["size"] != size or abs(row["mtime_ns"] - mtime_ns) > MTIME_TOLERANCE_NS:
            tier, reason, copies = "unchecked", "Changed since the last check", []
        else:
            tier, reason, copies = _classify(row)
        out.append({"rel_path": rel, "size": size, "kind": kind, "tier": tier, "reason": reason, "copies": copies,
                    "asset_id": row["asset_id"] if row is not None and tier != "unchecked" else None,
                    "body_id": bodies.get(key)})
    _infer_cameras(out)
    out.sort(key=lambda f: (RANK[f["tier"]], f["rel_path"]))
    return out


def _infer_cameras(files: list[dict]) -> None:
    """Camera videos often carry no body: use the most common camera of the same card folder."""
    votes: dict[str, Counter] = {}
    for f in files:
        if f["body_id"] is not None:
            votes.setdefault(str(PurePosixPath(f["rel_path"]).parent), Counter())[f["body_id"]] += 1
    for f in files:
        if f["body_id"] is None:
            v = votes.get(str(PurePosixPath(f["rel_path"]).parent))
            if v:
                f["body_id"] = v.most_common(1)[0][0]


def _counts(files: Iterable[dict]) -> dict[str, dict[str, int]]:
    counts = {t: {"files": 0, "bytes": 0} for t in TIERS}
    for f in files:
        counts[f["tier"]]["files"] += 1
        counts[f["tier"]]["bytes"] += f["size"]
    return counts


def camera_rows(files: list[dict], names: dict[int, str]) -> list[dict]:
    """Per camera: tier counts and its worst tier, worst cameras first."""
    by: dict[str, list[dict]] = {}
    for f in files:
        by.setdefault(str(f["body_id"]) if f["body_id"] is not None else UNKNOWN_CAMERA, []).append(f)
    out = []
    for key, members in by.items():
        name = "Unknown camera" if key == UNKNOWN_CAMERA else names.get(int(key), "Camera")
        out.append({"camera": key, "name": name, "tier": worst(f["tier"] for f in members),
                    "files": len(members), "counts": _counts(members)})
    out.sort(key=lambda c: (RANK.get(c["tier"], len(TIERS)), c["name"]))
    return out


def copies_by_volume(files: list[dict]) -> list[dict]:
    """Each other (non-CARD) drive holding a verified copy of the card's files, most files first."""
    by: dict[str, dict] = {}
    for f in files:
        for c in f["copies"]:
            v = by.setdefault(c["volume_id"], {"volume_id": c["volume_id"], "label": c["label"], "role": c["role"],
                                               "files": 0, "bytes": 0})
            v["files"] += 1
            v["bytes"] += f["size"]
    return sorted(by.values(), key=lambda v: (-v["files"], v["label"]))


def card_summary(conn: sqlite3.Connection, volume_id: str | None, *, mount: Path | None = None,
                 files: list[dict] | None = None) -> dict:
    """Tier counts, the card's worst tier, and its cameras."""
    files = card_files(conn, volume_id, mount=mount) if files is None else files
    return {
        "tier": worst(f["tier"] for f in files),
        "files": len(files),
        "bytes": sum(f["size"] for f in files),
        "counts": _counts(files),
        "cameras": camera_rows(files, camera_names(conn)),
        "copies_by_volume": copies_by_volume(files),
        "checked_at": _checked_at(conn, volume_id) if volume_id else None,
    }


def _checked_at(conn: sqlite3.Connection, volume_id: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(COALESCE(finished_at, created_at)) AS at FROM jobs WHERE source_volume_id = ? "
        "AND kind IN ('index_volume', 'baseline_hash', 'card_ingest') AND state = 'done'",
        (volume_id,),
    ).fetchone()
    return row["at"] if row else None


def list_cards(conn: sqlite3.Connection, drive_rows: list[dict]) -> dict:
    """Every card: plugged-in ones (registered or new) from `drive_rows`, then remembered ones. Cameras are
    totalled over plugged-in cards only (a remembered card may have been formatted since)."""
    names = camera_names(conn)
    cards = []
    connected_files: list[dict] = []
    seen: set[str] = set()
    for d in drive_rows:
        is_card = str(d.get("role") or "").startswith("CARD") or d.get("looks_like_card")
        if not (is_card and d.get("mounted") and d.get("mount")):
            continue
        vid = d.get("volume_id")
        files = card_files(conn, vid, mount=Path(d["mount"]))
        connected_files += files
        cards.append(_card_row(conn, d, vid, files))
        if vid:
            seen.add(vid)
    for v in repo.list_volumes(conn):
        if v["role"] != "CARD" or v["volume_id"] in seen:
            continue
        files = card_files(conn, v["volume_id"])
        cards.append(_card_row(conn, {"label": v["label"], "mount": None, "mounted": False,
                                      "last_seen_at": v["last_seen_at"]}, v["volume_id"], files))
    return {"cards": cards, "cameras": camera_rows(connected_files, names)}


def _card_row(conn: sqlite3.Connection, d: dict, vid: str | None, files: list[dict]) -> dict:
    summary = card_summary(conn, vid, files=files)
    label = d.get("label") or (Path(d["mount"]).name if d.get("mount") else "Card")
    return {"volume_id": vid, "label": label, "mount": d.get("mount"), "mounted": bool(d.get("mounted")),
            "last_seen_at": d.get("last_seen_at"), "registered": vid is not None, **summary}


__all__ = ["EMPTY", "RANK", "TIERS", "camera_names", "camera_rows", "card_files", "card_summary", "check_card",
           "list_cards", "worst"]
