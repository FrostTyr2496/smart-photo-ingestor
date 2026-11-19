"""Actions: a ranked list of what to do next, by risk to the photos (spec `actions`, D-084).

``compute`` reads the catalog (and, when present, `<home>/metrics.sqlite` and the newest duplicate report) and returns
action dicts in the `GET /api/actions` contract. It never runs anything, never touches a drive and writes nothing.
Each rule is one function over aggregate SQL (the catalog holds tens of thousands of groups). A rule whose inputs are
missing quietly yields nothing. Dismiss and snooze state is applied by ``apply_state`` from the ``actions_state``
setting (JSON ``{id: {dismissed: bool, until: iso}}``).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from photovault import previews, targets
from photovault.catalog import repo
from photovault.protocol import human_bytes

SEVERITIES = ("critical", "high", "normal", "low")
STATE_KEY = "actions_state"
STALE_DAYS = 30          # drive_stale: a drive with single-copy files not seen for this long
ERROR_DAYS = 90          # drive_errors: only incidents newer than this
FAILED_DAYS = 60         # job_paused: a failed job older than this is history, not an action
DEFAULT_WINDOW_S = 1800  # locations_suggested: a fix this close in time counts as a suggestion (match_window_s)
READ_OPS = ("read_full", "read_partial", "read_verify")

# A file is backed up when a verified copy sits on one of these (D-003). A copy on a CARD never counts, and LEGACY
# is not a backup (cards.py ranks it as "needs backup", D-046).
BACKUP_ROLES = ("WORKING", "MASTER")


# --------------------------------------------------------------------------- helpers


def _n(value: int) -> str:
    return f"{value:,}"


def _files(n: int) -> str:
    return f"{_n(n)} file" + ("" if n == 1 else "s")


def _parse(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        when = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def _age_days(now: datetime, iso: str | None) -> int | None:
    when = _parse(iso)
    return None if when is None else max(0, (now - when).days)


def _ago(days: int | None) -> str:
    if days is None:
        return "Never seen"
    if days == 0:
        return "Last seen today"
    return f"Last seen {days} day" + ("" if days == 1 else "s") + " ago"


def _need(vol: Mapping[str, Any], mounted: Mapping[str, str | None]) -> dict:
    return {"volume_id": vol["volume_id"], "label": vol["label"], "role": vol["role"],
            "mounted": vol["volume_id"] in mounted}


def _mount_of(volume_id: str, mounted: Mapping[str, str | None], placeholder: str) -> str:
    """The real mount path when the drive is plugged in, else a placeholder the user fills in."""
    return mounted.get(volume_id) or placeholder


def _action(id_: str, kind: str, severity: str, title: str, why: list[str], needs: list[dict], steps: list[str],
            do: dict, cli: str | None, *, files: int = 0, nbytes: int = 0, age: int | None = None,
            **evidence: Any) -> dict:
    return {"id": id_, "kind": kind, "severity": severity, "title": title, "why": why, "needs": needs,
            "steps": steps, "do": do, "cli": cli, "evidence": {"files": files, "bytes": nbytes, **evidence},
            "snoozed_until": None, "_age": age or 0}


def _ui(label: str, href: str) -> dict:
    return {"label": label, "kind": "ui", "href": href}


def _run(label: str, cmd: str, args: dict) -> dict:
    return {"label": label, "kind": "run", "endpoint": "/api/run", "body": {"cmd": cmd, "args": args}}


NONE = {"label": "", "kind": "none"}


def _volumes(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    return {v["volume_id"]: v for v in repo.list_volumes(conn)}


def _metrics(home: Path | None) -> sqlite3.Connection | None:
    """The metrics store, read-only, or None when there is none (it is created by the CLI, never here)."""
    if home is None:
        return None
    path = Path(home) / "metrics.sqlite"
    if not path.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT 1 FROM incidents LIMIT 1")
        return conn
    except sqlite3.Error:
        return None


def _error_drives(home: Path | None) -> set[str]:
    """Volume keys with read errors on record (metrics)."""
    conn = _metrics(home)
    if conn is None:
        return set()
    try:
        marks = ",".join("?" * len(READ_OPS))
        rows = conn.execute(f"SELECT volume_key FROM daily WHERE errors > 0 AND op IN ({marks}) "
                            f"UNION SELECT volume_key FROM incidents WHERE op IN ({marks})", READ_OPS * 2).fetchall()
        return {r[0] for r in rows}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


# --------------------------------------------------------------------------- rules: data at risk


def _working_target(vols: Mapping[str, sqlite3.Row], mounted: Mapping[str, str | None]) -> sqlite3.Row | None:
    """The drive to back a card up to: a plugged-in WORKING drive, else the one seen most recently."""
    working = [v for v in vols.values() if v["role"] == "WORKING"]
    working.sort(key=lambda v: (v["volume_id"] not in mounted, -(_parse(v["last_seen_at"]) or datetime.min.replace(
        tzinfo=UTC)).timestamp()))
    return working[0] if working else None


def _recommend(conn, home, mounted, nbytes: int, source_id: str | None, exclude=frozenset()) -> dict | None:
    """The target recommendation (rec-1, D-103) when a drive is plugged in; None when it cannot be computed."""
    if not any(mounted.values()):
        return None
    try:
        return targets.recommend(conn, home, mounted, planned_bytes=nbytes, source_volume_id=source_id, exclude=exclude)
    except sqlite3.Error:
        return None


def card_unbacked(conn, now, mounted, home, vols) -> list[dict]:
    rows = conn.execute(
        f"""
        SELECT i.volume_id, COUNT(*) AS files, COALESCE(SUM(i.size), 0) AS bytes FROM instances i
        JOIN volumes v ON v.volume_id = i.volume_id AND v.role = 'CARD'
        WHERE i.state != 'missing' AND i.asset_id IS NOT NULL AND NOT EXISTS (
          SELECT 1 FROM instances o JOIN volumes ov ON ov.volume_id = o.volume_id
          WHERE o.asset_id = i.asset_id AND o.state = 'verified'
            AND ov.role IN ({",".join("?" * len(BACKUP_ROLES))}))
        GROUP BY i.volume_id
        """, BACKUP_ROLES).fetchall()
    totals = {r["volume_id"]: r["n"] for r in conn.execute(
        "SELECT volume_id, COUNT(*) AS n FROM instances WHERE state != 'missing' GROUP BY volume_id")}
    target = _working_target(vols, mounted)
    out = []
    for r in rows:
        vol = vols[r["volume_id"]]
        label, age = vol["label"], _age_days(now, vol["last_seen_at"])
        rec = _recommend(conn, home, mounted, r["bytes"], vol["volume_id"])
        pair = rec["pair"] if rec else {}
        first = vols.get(pair.get("read")) if pair.get("read") else target   # rec-1: the recommended drive, else the old pick
        second = vols.get(pair.get("copy")) if pair.get("copy") else None
        where = f" to {first['label']}" if first else ""
        needs = ([_need(vol, mounted)] + ([_need(first, mounted)] if first else [])
                 + ([_need(second, mounted)] if second else []))
        src = _mount_of(vol["volume_id"], mounted, f"/Volumes/{label}")
        dst = (_mount_of(first["volume_id"], mounted, f"/Volumes/{first['label']}") if first
               else "/Volumes/<working drive>")
        back_step = (f"Back it up{where}, with a trailing copy onto {second['label']} so it turns green" if second
                     else f"Back it up{where}")
        steps = ([] if vol["volume_id"] in mounted else [f"Plug in {label}"]) + [
            back_step, "Check the card shows green before formatting it in camera"]
        out.append(_action(
            f"card-unbacked:{vol['volume_id']}", "card_unbacked", "critical",
            f"Back up {label}: {_files(r['files'])} ({human_bytes(r['bytes'])}) exist only on this card",
            [f"{_n(r['files'])} of {_n(totals.get(vol['volume_id'], r['files']))} files on {label} have no verified "
             "copy on a working or master drive", _ago(age)] + ([pair["note"]] if pair.get("note") else []),
            needs, steps, _ui("Back up", f"#/cards/{vol['volume_id']}"),
            f'uv run photovault ingest --source "{src}" --target "{dst}" --name "<shoot name>" --dry-run',
            files=r["files"], nbytes=r["bytes"], age=age))
    return out


def card_make_green(conn, now, mounted, home, vols) -> list[dict]:
    """Yellow files (one verified copy, D-089): copy them onto a connected second drive (bk-7, D-108)."""
    from photovault import backup  # local import: backup imports cards and targets

    out = []
    for vol in vols.values():
        if vol["role"] != "CARD":
            continue
        plan = backup.plan_make_green(conn, None, card_volume_id=vol["volume_id"], mounted=dict(mounted), home=home)
        if not plan["files"]:
            continue
        label = vol["label"]
        ok = [r for r in plan["sources"] if r["ok"]]
        stuck = [r for r in plan["sources"] if not r["ok"]]
        targets_ = list(dict.fromkeys(r["target"]["label"] for r in ok))
        title = f"{_files(plan['files'])} on {label} {'has' if plan['files'] == 1 else 'have'} one copy"
        if ok:
            title += f": copy them to {_list_names(targets_)} to make them green"
        why = [f"{_n(plan['files'])} files ({human_bytes(plan['bytes'])}) on {label} are on one drive, so the card shows "
               "yellow (green needs a backup drive, or two working drives)"]
        why += [f"{r['source']['label']}: {r['reason']}" for r in stuck]
        why += [f"{r['source']['label']} -> {r['target']['label']}: {_n(r['files'])} files, {human_bytes(r['bytes'])}"
                for r in ok]
        needs = []
        for r in ok:
            needs += [_need(vols[r["source"]["volume_id"]], mounted), _need(vols[r["target"]["volume_id"]], mounted)]
        steps = ([f"Copy onto {_list_names(targets_)}"] if ok else ["Plug in a second working or backup drive"]) + \
            (["Plug in the drives that hold the other files"] if ok and stuck else []) + \
            [f"Check {label} shows green"]
        do = _run("Make green", "backup", {"card": vol["volume_id"], "make_green": True}) if ok \
            else _ui("Open card", f"#/cards/{vol['volume_id']}")
        out.append(_action(
            f"card-one-copy:{vol['volume_id']}", "card_one_copy", "high", title, why, needs, steps, do,
            f"uv run photovault backup --card {vol['volume_id']} --make-green --dry-run",
            files=plan["files"], nbytes=plan["bytes"], age=_age_days(now, vol["last_seen_at"])))
    return out


def _list_names(names: list[str]) -> str:
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def card_unindexed(conn, now, mounted, home, vols) -> list[dict]:
    stats = {r["volume_id"]: r for r in conn.execute(
        """
        SELECT volume_id, SUM(state != 'missing') AS files,
               SUM(state = 'indexed' AND asset_id IS NULL) AS unhashed,
               SUM(CASE WHEN state = 'indexed' AND asset_id IS NULL THEN size ELSE 0 END) AS unhashed_bytes
        FROM instances GROUP BY volume_id""")}
    indexed = {r[0] for r in conn.execute(
        "SELECT DISTINCT source_volume_id FROM jobs WHERE kind = 'index_volume' AND state = 'done'")}
    out = []
    for vol in vols.values():
        if vol["role"] != "CARD":
            continue
        s = stats.get(vol["volume_id"])
        files = (s["files"] or 0) if s else 0
        unhashed = (s["unhashed"] or 0) if s else 0
        never = vol["volume_id"] not in indexed and files == 0
        if not never and not unhashed:
            continue
        label, age = vol["label"], _age_days(now, vol["last_seen_at"])
        why = ([f"{label} has never been indexed, so nobody knows what is on it or whether it is backed up"] if never
               else [f"{_n(unhashed)} of {_n(files)} files on {label} are indexed but not hashed, so their backup "
                     "state is unknown"])
        mount = mounted.get(vol["volume_id"])
        do = (_run("Check card", "card_check", {"mount": mount}) if mount
              else _ui("Open card", f"#/cards/{vol['volume_id']}"))
        out.append(_action(
            f"card-unindexed:{vol['volume_id']}", "card_unindexed", "high",
            f"Check {label}: its backup state is unknown",
            why + [_ago(age)], [_need(vol, mounted)],
            ([] if mount else [f"Plug in {label}"]) + ["Check it (index and hash, read-only)"], do,
            f'uv run photovault cards check "{mount or "/Volumes/" + label}"',
            files=unhashed or files, nbytes=(s["unhashed_bytes"] or 0) if s else 0, age=age))
    return out


def _single_copy_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Per non-CARD volume: assets with exactly one verified instance overall (one aggregate query)."""
    return conn.execute(
        """
        WITH one AS (SELECT asset_id, MIN(volume_id) AS volume_id, MAX(size) AS size FROM instances
                     WHERE state = 'verified' AND asset_id IS NOT NULL GROUP BY asset_id HAVING COUNT(*) = 1)
        SELECT one.volume_id, COUNT(*) AS files, COALESCE(SUM(one.size), 0) AS bytes
        FROM one JOIN volumes v ON v.volume_id = one.volume_id AND v.role != 'CARD'
        GROUP BY one.volume_id
        """).fetchall()


def single_copy(conn, now, mounted, home, vols, singles=None) -> list[dict]:
    errors = _error_drives(home)
    out = []
    for r in singles if singles is not None else _single_copy_rows(conn):
        vol = vols[r["volume_id"]]
        hurt = vol["volume_id"] in errors
        label = vol["label"]
        rec = _recommend(conn, home, mounted, r["bytes"], None, exclude={vol["volume_id"]})
        best = next((c for c in rec["candidates"] if c["usable"]), None) if rec else None
        out.append(_action(
            f"single-copy:{vol['volume_id']}", "single_copy", "critical" if hurt else "high",
            f"{_files(r['files'])} ({human_bytes(r['bytes'])}) on {label} have no second copy",
            [f"Each of these files has exactly one verified copy, on {label}"]
            + ([f"{label} has recorded read errors, so a copy is urgent"] if hurt else [])
            + [_ago(_age_days(now, vol["last_seen_at"]))],
            [_need(vol, mounted)],
            [f"Plug in {label}" if vol["volume_id"] not in mounted else (
                f"Copy {label} onto {best['label']} ({', '.join(best['reasons'][:2])})" if best
                else f"Copy {label} to a second drive"),
             "Assign a master drive and replicate to it"], NONE,
            (f'uv run photovault backup --from-folder "{_mount_of(vol["volume_id"], mounted, "/Volumes/" + label)}/<folder>" '
             f'--to "{best["mount"]}" --dry-run' if best and vol["volume_id"] in mounted else None),
            files=r["files"], nbytes=r["bytes"], age=_age_days(now, vol["last_seen_at"])))
    return out


def drive_errors(conn, now, mounted, home, vols) -> list[dict]:
    store = _metrics(home)
    if store is None:
        return []
    cutoff = (now - timedelta(days=ERROR_DAYS)).strftime("%Y-%m-%dT%H:%M:%S")
    marks = ",".join("?" * len(READ_OPS))
    try:
        rows = store.execute(
            f"SELECT volume_key, COUNT(*) AS n, MAX(ts) AS last FROM incidents WHERE op IN ({marks}) AND ts >= ? "
            "GROUP BY volume_key", (*READ_OPS, cutoff)).fetchall()
        paths = {r["volume_key"]: [p[0] for p in store.execute(
            f"SELECT path FROM incidents WHERE volume_key = ? AND op IN ({marks}) AND ts >= ? "
            "ORDER BY incident_id DESC LIMIT 500", (r["volume_key"], *READ_OPS, cutoff))] for r in rows}
    except sqlite3.Error:
        return []
    finally:
        store.close()
    out = []
    for r in rows:
        vol = vols.get(r["volume_key"])
        if vol is None:
            continue
        prefix = _common_prefix(paths[r["volume_key"]], vol["last_mount_path"])
        label, age = vol["label"], _age_days(now, r["last"])
        mount = _mount_of(vol["volume_id"], mounted, f"/Volumes/{label}")
        skip = f' --skip-prefix "{prefix}"' if prefix else ""
        out.append(_action(
            f"drive-errors:{vol['volume_id']}", "drive_errors", "high",
            f"{label} hit {_n(r['n'])} read error{'' if r['n'] == 1 else 's'}"
            + (f" in {prefix}" if prefix else ""),
            [f"The last read error was {age} day{'' if age == 1 else 's'} ago" if age is not None else "Read errors recorded",
             "A drive that fails reads can get worse: copy what is readable first"]
            + ([f"Errors are in the folder {prefix}"] if prefix else []),
            [_need(vol, mounted)],
            ([] if vol["volume_id"] in mounted else [f"Plug in {label}"])
            + ["Hash the rest, skipping the damaged folder", "Copy what is readable to another drive"],
            _ui("Open drive", f"#/drive/{vol['volume_id']}"),
            f'caffeinate -i uv run photovault hash "{mount}"{skip}', files=r["n"], age=age, folder=prefix))
    return out


def _common_prefix(paths: list[str], mount: str | None) -> str:
    """The folder (two levels under the drive) most read errors sit in, relative to the drive, or ''."""
    from collections import Counter
    base = (mount or "").rstrip("/") + "/"
    tally: Counter[str] = Counter()
    for p in paths:
        if mount and p.startswith(base):
            parts = p[len(base):].split("/")[:-1]
            if parts:
                tally["/".join(parts[:2])] += 1
    return tally.most_common(1)[0][0] if tally else ""


# --------------------------------------------------------------------------- rules: unfinished work


def job_paused(conn, now, mounted, home, vols) -> list[dict]:
    rows = conn.execute(
        """
        SELECT j.* FROM jobs j WHERE j.state IN ('paused', 'failed') AND NOT EXISTS (
          SELECT 1 FROM jobs n WHERE n.job_id > j.job_id AND n.kind = j.kind
            AND COALESCE(n.source_volume_id, '') = COALESCE(j.source_volume_id, '')
            AND COALESCE(n.target_volume_id, '') = COALESCE(j.target_volume_id, '') AND n.state = 'done')
        ORDER BY j.job_id DESC LIMIT 50
        """).fetchall()
    names = {"card_ingest": "Import", "index_volume": "Index", "baseline_hash": "Hash", "replicate": "Copy",
             "fixity_audit": "Check"}
    out = []
    for j in rows:
        age = _age_days(now, j["finished_at"] or j["created_at"])
        if j["state"] == "failed" and (age is None or age > FAILED_DAYS):
            continue
        vol = vols.get(j["source_volume_id"] or "")
        what = names.get(j["kind"], j["kind"])
        label = vol["label"] if vol else "a drive"
        progress = ""
        if j["kind"] == "card_ingest":
            c = conn.execute("SELECT COUNT(*) AS n, SUM(state IN ('committed', 'skipped_dup')) AS done "
                             "FROM job_items WHERE job_id = ?", (j["job_id"],)).fetchone()
            if c["n"]:
                progress = f"{_n(c['done'] or 0)} of {_n(c['n'])} files done"
        paused = j["state"] == "paused"
        out.append(_action(
            f"job-paused:{j['job_id']}", "job_paused", "normal",
            f"{what} of {label} {'is paused' if paused else 'failed'}" + (f" ({progress})" if progress else ""),
            [f"Job {j['job_id']} {'was paused' if paused else 'stopped with an error'} {age} days ago"
             if age is not None else f"Job {j['job_id']} is {j['state']}"]
            + ([progress] if progress else []),
            [_need(vol, mounted)] if vol else [],
            ([f"Plug in {label}"] if vol and vol["volume_id"] not in mounted else []) + ["Resume the job"],
            _run("Resume", "resume", {"job_id": int(j["job_id"])}) if paused else _ui("Open Tasks", "#/"),
            f"uv run photovault resume {j['job_id']}" if paused else "uv run photovault jobs", age=age))
    return out


def hash_incomplete(conn, now, mounted, home, vols) -> list[dict]:
    paused = {r[0] for r in conn.execute(
        "SELECT source_volume_id FROM jobs WHERE kind = 'baseline_hash' AND state = 'paused'")}
    rows = conn.execute(
        """
        SELECT i.volume_id, SUM(i.state != 'missing') AS files,
               SUM(i.state = 'indexed' AND i.asset_id IS NULL) AS pending,
               SUM(CASE WHEN i.state = 'indexed' AND i.asset_id IS NULL THEN i.size ELSE 0 END) AS bytes
        FROM instances i JOIN volumes v ON v.volume_id = i.volume_id AND v.role != 'CARD'
        GROUP BY i.volume_id HAVING pending > 0
        """).fetchall()
    out = []
    for r in rows:
        if r["volume_id"] in paused:
            continue
        vol = vols[r["volume_id"]]
        label, mount = vol["label"], mounted.get(r["volume_id"])
        share = round(100 * r["pending"] / r["files"]) if r["files"] else 0
        out.append(_action(
            f"hash-incomplete:{vol['volume_id']}", "hash_incomplete", "normal",
            f"Hash {label}: {_files(r['pending'])} ({human_bytes(r['bytes'])}) not hashed yet",
            [f"{_n(r['pending'])} of {_n(r['files'])} indexed files ({share} %) have no hash",
             "Duplicate reports and copy checks need hashes"],
            [_need(vol, mounted)],
            ([] if mount else [f"Plug in {label}"]) + ["Run the hash step"],
            _run("Hash", "hash", {"mount": mount}) if mount else _ui("Open drive", f"#/drive/{vol['volume_id']}"),
            f'caffeinate -i uv run photovault hash "{mount or "/Volumes/" + label}"',
            files=r["pending"], nbytes=r["bytes"], age=_age_days(now, vol["last_seen_at"])))
    return out


def drive_stale(conn, now, mounted, home, vols, singles=None) -> list[dict]:
    out = []
    for r in singles if singles is not None else _single_copy_rows(conn):
        vol = vols[r["volume_id"]]
        age = _age_days(now, vol["last_seen_at"])
        if vol["volume_id"] in mounted or age is None or age <= STALE_DAYS:
            continue
        label = vol["label"]
        out.append(_action(
            f"drive-stale:{vol['volume_id']}", "drive_stale", "normal",
            f"Plug in {label}: not seen for {age} days",
            [f"{label} holds {_files(r['files'])} ({human_bytes(r['bytes'])}) with no second copy",
             f"Last seen {age} days ago, so its files have not been checked since"],
            [_need(vol, mounted)], [f"Plug in {label}", "Run Index so the catalog matches the drive"],
            _ui("Open drive", f"#/drive/{vol['volume_id']}"), f'uv run photovault index "/Volumes/{label}"',
            files=r["files"], nbytes=r["bytes"], age=age))
    return out


def sidecars_dirty(conn, now, mounted, home, vols) -> list[dict]:
    rows = conn.execute(
        """
        SELECT i.volume_id, COUNT(DISTINCT m.group_id) AS groups FROM group_meta m
        JOIN assets a ON a.group_id = m.group_id AND a.group_role = 'primary'
        JOIN instances i ON i.asset_id = a.asset_id AND i.state IN ('indexed', 'verified')
        JOIN volumes v ON v.volume_id = i.volume_id AND v.role = 'WORKING'
        WHERE m.dirty = 1 GROUP BY i.volume_id
        """).fetchall()
    out = []
    for r in rows:
        vol = vols[r["volume_id"]]
        label, mount = vol["label"], mounted.get(r["volume_id"])
        out.append(_action(
            f"sidecars-dirty:{vol['volume_id']}", "sidecars_dirty", "normal",
            f"Write sidecars on {label}: {_n(r['groups'])} photo{'' if r['groups'] == 1 else 's'} changed",
            [f"{_n(r['groups'])} photos on {label} have edited metadata that is not in their XMP files yet"],
            [_need(vol, mounted)],
            ([] if mount else [f"Plug in {label}"]) + ["Write the sidecars (a dry run shows what changes)"],
            _run("Write sidecars", "meta_sidecars", {"mount": mount}) if mount
            else _ui("Open drive", f"#/drive/{vol['volume_id']}"),
            f'uv run photovault meta sidecars --volume "{mount or "/Volumes/" + label}"', files=r["groups"]))
    return out


# --------------------------------------------------------------------------- rules: housekeeping


def metadata_stale(conn, now, mounted, home, vols) -> list[dict]:
    rows = conn.execute(
        """
        SELECT i.volume_id, COUNT(DISTINCT g.group_id) AS groups FROM instances i
        JOIN volumes v ON v.volume_id = i.volume_id AND v.role != 'CARD'
        JOIN assets a ON a.asset_id = i.asset_id AND a.group_role = 'primary'
        JOIN capture_groups g ON g.group_id = a.group_id
        LEFT JOIN group_meta m ON m.group_id = g.group_id
        WHERE i.state IN ('indexed', 'verified') AND (m.group_id IS NULL OR g.subsec IS NULL)
        GROUP BY i.volume_id
        """).fetchall()
    out = []
    for r in rows:
        vol = vols[r["volume_id"]]
        label, mount = vol["label"], mounted.get(r["volume_id"])
        out.append(_action(
            f"metadata-stale:{vol['volume_id']}", "metadata_stale", "low",
            f"Read metadata on {label}: {_n(r['groups'])} photo{'' if r['groups'] == 1 else 's'} lack some",
            [f"{_n(r['groups'])} photos on {label} have no metadata row or no sub-second time",
             "Without it, burst order and shoot grouping are less exact"],
            [_need(vol, mounted)],
            ([] if mount else [f"Plug in {label}"]) + ["Run Scan metadata again"],
            _run("Scan metadata", "meta_scan", {"mount": mount, "rescan": True}) if mount
            else _ui("Open drive", f"#/drive/{vol['volume_id']}"),
            f'uv run photovault meta scan --volume "{mount or "/Volumes/" + label}" --rescan', files=r["groups"]))
    return out


def locations_suggested(conn, now, mounted, home, vols) -> list[dict]:
    """Groups with no location and a GPS fix within the match window of their capture instant (a close fix is a
    `high` or `medium` suggestion). The instant is the capture time minus its offset (body clocks are not applied:
    this is a count, the Photos pane shows the exact suggestion)."""
    window = int(repo.get_setting(conn, "match_window_s", str(DEFAULT_WINDOW_S)) or DEFAULT_WINDOW_S)
    default_off = repo.get_setting(conn, "default_offset", "+00:00") or "+00:00"
    off = "COALESCE(g.capture_offset, :off)"
    off_s = (f"(CASE substr({off}, 1, 1) WHEN '-' THEN -1 ELSE 1 END) * "
             f"(CAST(substr({off}, 2, 2) AS INTEGER) * 3600 + CAST(substr({off}, 5, 2) AS INTEGER) * 60)")
    fmt = "'%Y-%m-%dT%H:%M:%S+00:00'"
    utc = f"(CAST(strftime('%s', g.capture_time) AS INTEGER) - ({off_s}))"
    try:
        rows = conn.execute(
            f"""
            SELECT substr(g.capture_time, 1, 10) AS day, COUNT(*) AS n FROM capture_groups g
            LEFT JOIN group_meta m ON m.group_id = g.group_id
            WHERE g.capture_time IS NOT NULL AND (m.group_id IS NULL OR m.lat IS NULL OR m.lon IS NULL)
              AND EXISTS (SELECT 1 FROM geo_points p WHERE p.utc_time BETWEEN
                          strftime({fmt}, {utc} - {window}, 'unixepoch') AND strftime({fmt}, {utc} + {window}, 'unixepoch'))
            GROUP BY day ORDER BY n DESC, day DESC
            """, {"off": default_off}).fetchall()
    except sqlite3.Error:
        return []
    total = sum(r["n"] for r in rows)
    if not total:
        return []
    top = rows[0]
    return [_action(
        "locations-suggested", "locations_suggested", "low",
        f"Add locations: {_n(total)} photo{'' if total == 1 else 's'} have a place suggested",
        [f"{_n(total)} photos with no location have a GPS fix from another source within {window // 60} minutes",
         f"The biggest day is {top['day']} with {_n(top['n'])} photos ({len(rows)} day{'' if len(rows) == 1 else 's'})"],
        [], ["Open Photos filtered to Missing location", "Check and accept the suggestions"],
        _ui("Open Photos", f"#/photos?missing=gps&day={top['day']}"), None, files=total, top_day=top["day"],
        days=len(rows))]


def _newest_plan(home: Path | None) -> dict | None:
    """The newest `<home>/plans/shoots-*.json` as a dict (one file read), or None."""
    plans = Path(home) / "plans" if home is not None else None
    found = sorted(plans.glob("shoots-*.json")) if plans is not None and plans.is_dir() else []
    if not found:
        return None
    try:
        data = json.loads(found[-1].read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _undecided_shoots(conn: sqlite3.Connection, plan: dict) -> int:
    """Shoots in the plan that are not locked and have no review decision stored for their proposal."""
    decided = {r[0] for r in conn.execute("SELECT proposal_id FROM shoot_plan_decisions")}
    shoots = plan.get("shoots")
    if not isinstance(shoots, list):
        return 0
    return sum(1 for s in shoots if isinstance(s, dict) and not s.get("locked") and not s.get("state")
               and s.get("proposal_id") not in decided)


def shoots_review(conn, now, mounted, home, vols) -> list[dict]:
    """Photos not in a shoot (`capture_groups.shoot_id IS NULL`). With a plan file under `<home>/plans`, open the
    review (and say how many of its shoots are still undecided); without one, offer to make the plan (sh-2)."""
    n = conn.execute("SELECT COUNT(*) FROM capture_groups WHERE shoot_id IS NULL").fetchone()[0]
    if not n:
        return []
    plan = _newest_plan(home)
    why = [f"{_n(n)} photos belong to no shoot"]
    if plan is None:
        return [_action(
            "shoots-review", "shoots_review", "low", f"Make a shoot plan: {_n(n)} photos are not in a shoot yet",
            why + ["No shoot plan has been made yet"], [], ["Make the plan", "Review it, then apply"],
            _run("Make a shoot plan", "shoots_plan", {}), "uv run photovault --json shoots plan", files=n)]
    todo = _undecided_shoots(conn, plan)
    why.append("A shoot plan is ready to review" + (f": {_n(todo)} shoot{'' if todo == 1 else 's'} not decided yet"
                                                    if todo else ""))
    return [_action(
        "shoots-review", "shoots_review", "low", f"Review shoots: {_n(n)} photos are not in a shoot yet", why, [],
        ["Open the shoot review", "Accept, split or merge the proposals"], _ui("Review shoots", "#/review/shoots"),
        None, files=n, undecided=todo)]


def _span(first: str, last: str) -> str:
    """'2-7 Apr' (same month), '30 Mar-2 Apr', or one date; from ISO dates."""
    try:
        a, b = datetime.fromisoformat(first), datetime.fromisoformat(last)
    except ValueError:
        return first if first == last else f"{first} to {last}"
    if a == b:
        return f"{a.day} {a:%b}"
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}\u2013{b.day} {a:%b}"
    return f"{a.day} {a:%b}\u2013{b.day} {b:%b}"


def clock_suggested(conn, now, mounted, home, vols) -> list[dict]:
    """The time-zone banner's suggestions (sp-7b, `clock.suggest_offsets`) as actions, one per body and phone run.
    Applying one is a button in Photos' banner. Cost is that of `GET /api/clock-suggestions` (a pass over every
    dated frame of bodies with phone fixes); it is skipped when there are no phone points."""
    from photovault import clock
    out: list[dict] = []
    for s in clock.suggest_offsets(conn):
        n = int(s["frames"])
        mins = int(s["delta_s"]) // 60
        amount = f"{abs(mins) // 60} hour{'' if abs(mins) == 60 else 's'}" if mins % 60 == 0 else f"{abs(mins)} minutes"
        out.append(_action(
            f"clock:{s['body_id']}:{s['start_utc']}", "clock_suggested", "low",
            f"Align {s['body']} for {_span(s['first_date'], s['last_date'])}: {_n(n)} photo{'' if n == 1 else 's'}",
            [f"{s['body']}'s clock looks {amount} "
             f"{'behind' if mins > 0 else 'ahead'} of the phone on these days",
             f"{s['sessions_matched']} of {s['sessions_total']} sessions match the phone's track at that shift"],
            [], ["Open Photos", "Check the time banner and apply the suggestion"],
            _ui("Review in Photos", "#/photos"), None, files=n, body_id=s["body_id"], delta_s=s["delta_s"]))
    return out


def previews_missing(conn, now, mounted, home, vols) -> list[dict]:
    """act-4: a connected drive whose photos have no preview cached on this Mac yet. Only while it is connected:
    the cache is read from the drive, and only when the user presses the button."""
    if home is None:
        return []
    have = previews.cached_ids(home)
    out = []
    for vid, mount in mounted.items():
        vol = vols.get(vid)
        if vol is None or not mount:
            continue
        ids = {g for g, _ in previews.volume_primaries(conn, vid)}
        n = len(ids - have)
        if not n:
            continue
        label = vol["label"]
        out.append(_action(
            f"previews-missing:{vid}", "previews_missing", "low",
            f"Keep previews from {label} while it is connected: {_n(n)} photo{'' if n == 1 else 's'} have none on this Mac",
            [f"{_n(n)} of {_n(len(ids))} photos on {label} show a thumbnail only while {label} is connected",
             "Cached thumbnails are small (tens of KB each) and stay in PhotoVault's home folder"],
            [_need(vol, mounted)], [f"Keep {label} connected", "Run Cache previews (it only reads the drive)"],
            _run("Cache previews", "previews_cache", {"mount": mount}),
            f'uv run photovault previews cache "{mount}"', files=n))
    return out


_RECLAIM = re.compile(r"^- Reclaimable: .*\((\d+) bytes\)", re.M)
_CLUSTERS = re.compile(r"^- Duplicate clusters: (\d+) \((\d+) files\)", re.M)


def duplicates_reclaimable(conn, now, mounted, home, vols) -> list[dict]:
    """From the newest `<home>/reports/duplicates-*.md`. It informs only (dd-0 is still a proposal)."""
    reports = Path(home) / "reports" if home is not None else None
    found = sorted(reports.glob("duplicates-*.md")) if reports is not None and reports.is_dir() else []
    if not found:
        return []
    try:
        text = found[-1].read_text(encoding="utf-8")
        stamp = datetime.fromtimestamp(found[-1].stat().st_mtime, UTC)
    except OSError:
        return []
    r, c = _RECLAIM.search(text), _CLUSTERS.search(text)
    if not r or not c or not int(r.group(1)):
        return []
    nbytes, clusters = int(r.group(1)), int(c.group(1))
    age = max(0, (now - stamp).days)
    return [_action(
        "duplicates-reclaimable", "duplicates_reclaimable", "low",
        f"{human_bytes(nbytes)} of duplicates could be reclaimed",
        [f"The last duplicate report found {_n(clusters)} cluster{'' if clusters == 1 else 's'} ({_n(int(c.group(2)))} "
         f"files), {human_bytes(nbytes)} reclaimable", f"The report is {age} day{'' if age == 1 else 's'} old; "
         "run it again for current numbers", "This is information only: nothing is deleted"],
        [], ["Read the report", "Decide what to keep (no tool deletes duplicates yet)"], NONE,
        "uv run photovault report duplicates", nbytes=nbytes, files=int(c.group(2)), age=age)]


# --------------------------------------------------------------------------- compute and state


def compute(conn: sqlite3.Connection, *, now: datetime | None = None,
            mounted: Mapping[str, str | None] | Iterable[str] = (), home: Path | None = None) -> list[dict]:
    """Every action, most urgent first (severity, then bytes at risk, then age). `mounted` maps volume_id to its
    mount path (a bare iterable of ids also works). Dismiss and snooze are not applied here."""
    now = now or datetime.now(UTC)
    mount_map: Mapping[str, str | None] = mounted if isinstance(mounted, Mapping) else dict.fromkeys(mounted)
    vols = _volumes(conn)
    singles = _single_copy_rows(conn)
    rules: list[Callable[[], list[dict]]] = [
        lambda: card_unbacked(conn, now, mount_map, home, vols),
        lambda: card_make_green(conn, now, mount_map, home, vols),
        lambda: card_unindexed(conn, now, mount_map, home, vols),
        lambda: single_copy(conn, now, mount_map, home, vols, singles),
        lambda: drive_errors(conn, now, mount_map, home, vols),
        lambda: job_paused(conn, now, mount_map, home, vols),
        lambda: hash_incomplete(conn, now, mount_map, home, vols),
        lambda: drive_stale(conn, now, mount_map, home, vols, singles),
        lambda: sidecars_dirty(conn, now, mount_map, home, vols),
        lambda: metadata_stale(conn, now, mount_map, home, vols),
        lambda: locations_suggested(conn, now, mount_map, home, vols),
        lambda: shoots_review(conn, now, mount_map, home, vols),
        lambda: clock_suggested(conn, now, mount_map, home, vols),
        lambda: duplicates_reclaimable(conn, now, mount_map, home, vols),
        lambda: previews_missing(conn, now, mount_map, home, vols),
    ]
    out: list[dict] = []
    for rule in rules:
        try:
            out += rule()
        except sqlite3.Error:  # a rule whose inputs are missing yields nothing
            continue
    out.sort(key=lambda a: (SEVERITIES.index(a["severity"]), -a["evidence"]["bytes"], -a["_age"], a["id"]))
    return out


def load_state(conn: sqlite3.Connection) -> dict[str, dict]:
    try:
        state = json.loads(repo.get_setting(conn, STATE_KEY, "{}") or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in state.items() if isinstance(v, dict)} if isinstance(state, dict) else {}


def save_state(conn: sqlite3.Connection, state: dict[str, dict]) -> None:
    repo.set_setting(conn, STATE_KEY, json.dumps(state, sort_keys=True))


def hidden(entry: dict | None, now: datetime) -> bool:
    if not entry:
        return False
    if entry.get("dismissed"):
        return True
    until = _parse(entry.get("until"))
    return until is not None and until > now


def build_response(actions: list[dict], state: dict[str, dict], now: datetime) -> dict:
    """The `GET /api/actions` payload. Every action is listed, each with `dismissed` and `snoozed_until`; the
    severity counts cover only the visible ones, `snoozed` and `dismissed` count the hidden ones."""
    counts = {s: 0 for s in SEVERITIES} | {"snoozed": 0, "dismissed": 0}
    shown = []
    for a in actions:
        entry = state.get(a["id"])
        item = {k: v for k, v in a.items() if not k.startswith("_")} | {"dismissed": False}
        if entry and entry.get("dismissed"):
            counts["dismissed"] += 1
            item["dismissed"] = True
        elif hidden(entry, now):
            counts["snoozed"] += 1
            item["snoozed_until"] = entry["until"]
        else:
            counts[a["severity"]] += 1
        shown.append(item)
    return {"generated_at": now.isoformat(timespec="seconds"), "counts": counts, "actions": shown}


__all__ = ["BACKUP_ROLES", "SEVERITIES", "build_response", "compute", "hidden", "load_state", "save_state"]
