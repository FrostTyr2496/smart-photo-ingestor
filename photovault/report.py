"""Duplicate and coverage report (idx-4). Read-only: it only queries the catalog and writes one Markdown file.

Identity is the full SHA-256 (vault-rules 5), so only instances linked to an asset by `hash`/ingest count, and
only in state `verified`. CARD volumes are left out of duplicate clusters: a card copy next to a WORKING copy is
the normal import state, not something to reclaim. Nothing here deletes or moves a file (vault-rules 4); the
report only tells the user what could be reclaimed once they decide.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from photovault.protocol import human_bytes

VOLUMES_ROOT = Path("/Volumes")


@dataclass(frozen=True)
class ClusterInstance:
    volume_label: str
    volume_role: str
    rel_path: str
    size: int
    lr_referenced: bool


@dataclass(frozen=True)
class Cluster:
    """One asset with two or more verified instances on non-CARD volumes."""

    asset_id: int
    sha256: str
    size: int
    instances: tuple[ClusterInstance, ...]

    @property
    def reclaimable(self) -> int:
        """Bytes freed if all but one copy went away: size x (instances - 1)."""
        return self.size * (len(self.instances) - 1)


@dataclass(frozen=True)
class VolumeCoverage:
    """What the catalog knows about one volume. `only_here_*` counts assets with no verified copy elsewhere."""

    volume_id: str
    label: str
    role: str
    files: int  # instances not marked missing
    unhashed: int  # indexed but no full hash yet, so invisible to the duplicate search
    mismatch: int
    only_here_files: int
    only_here_bytes: int


NO_COPY = "no known copy"


@dataclass(frozen=True)
class UnreadableFile:
    """An instance whose full read failed with an I/O error (`mismatch`, no asset; idx-11) and where else it may be.

    `rescue` is a hint, not a verified copy: the damaged file has no full hash, so a match is either the same
    size and partial hash (a candidate only, vault-rules 5) or the same camera frame and role.
    """

    volume_label: str
    volume_role: str
    rel_path: str
    size: int
    rescue: str


def _labels(rows: list[sqlite3.Row]) -> str:
    return ", ".join(sorted({r["label"] for r in rows}))


def unreadable(conn: sqlite3.Connection) -> list[UnreadableFile]:
    """Every unreadable instance, by volume and path, with the best hint of a healthy copy on another volume."""
    from photovault.index import group_links  # the index job items hold each legacy file's group

    links: dict[str, dict[str, tuple[int, str]]] = {}
    out = []
    for r in conn.execute(
        """
        SELECT i.volume_id, i.rel_path, i.rel_path_key, i.size, i.partial_sha256, v.label, v.role
        FROM instances i JOIN volumes v ON v.volume_id = i.volume_id
        WHERE i.state = 'mismatch' AND i.asset_id IS NULL ORDER BY v.label, i.rel_path
        """
    ).fetchall():
        rescue = NO_COPY
        same = conn.execute(
            """
            SELECT DISTINCT v.label FROM assets a JOIN instances i ON i.asset_id = a.asset_id
            JOIN volumes v ON v.volume_id = i.volume_id
            WHERE a.size = ? AND a.partial_sha256 = ? AND i.state = 'verified' AND i.volume_id != ?
            """,
            (r["size"], r["partial_sha256"], r["volume_id"]),
        ).fetchall() if r["partial_sha256"] else []
        if same:
            rescue = f"likely copy on {_labels(same)} (same size and partial hash, unconfirmed)"
        else:
            if r["volume_id"] not in links:
                links[r["volume_id"]] = group_links(conn, r["volume_id"])
            group_id, role = links[r["volume_id"]].get(r["rel_path_key"], (None, None))
            frame = conn.execute(
                """
                SELECT DISTINCT v.label FROM assets a JOIN instances i ON i.asset_id = a.asset_id
                JOIN volumes v ON v.volume_id = i.volume_id
                WHERE a.group_id = ? AND a.group_role = ? AND i.state = 'verified' AND i.volume_id != ?
                """,
                (group_id, role, r["volume_id"]),
            ).fetchall() if group_id is not None else []
            if frame:
                rescue = f"same frame on {_labels(frame)} (same camera, shutter count and role; bytes may differ)"
        out.append(UnreadableFile(r["label"], r["role"], r["rel_path"], r["size"], rescue))
    return out


def duplicates(conn: sqlite3.Connection, *, volume_id: str | None = None) -> list[Cluster]:
    """Clusters of identical files, largest reclaimable first.

    With `volume_id`, only clusters with at least one copy on that volume; every copy is still listed, so the
    report shows where the other copies are.
    """
    having = "HAVING COUNT(*) >= 2"
    params: list[object] = []
    if volume_id is not None:
        having += " AND SUM(i2.volume_id = ?) >= 1"
        params.append(volume_id)
    rows = conn.execute(
        f"""
        SELECT i.asset_id, a.sha256, a.size AS asset_size, v.label, v.role, i.rel_path, i.size, i.lr_referenced
        FROM instances i
        JOIN volumes v ON v.volume_id = i.volume_id
        JOIN assets a ON a.asset_id = i.asset_id
        WHERE i.state = 'verified' AND v.role != 'CARD' AND i.asset_id IN (
            SELECT i2.asset_id FROM instances i2 JOIN volumes v2 ON v2.volume_id = i2.volume_id
            WHERE i2.state = 'verified' AND v2.role != 'CARD' AND i2.asset_id IS NOT NULL
            GROUP BY i2.asset_id {having}
        )
        ORDER BY i.asset_id, v.label, i.rel_path
        """,
        params,
    ).fetchall()
    grouped: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        grouped.setdefault(r["asset_id"], []).append(r)
    clusters = [
        Cluster(
            asset_id=asset_id,
            sha256=members[0]["sha256"],
            size=members[0]["asset_size"],
            instances=tuple(
                ClusterInstance(m["label"], m["role"], m["rel_path"], m["size"], bool(m["lr_referenced"]))
                for m in members
            ),
        )
        for asset_id, members in grouped.items()
    ]
    clusters.sort(key=lambda c: (-c.reclaimable, c.asset_id))
    return clusters


def coverage(conn: sqlite3.Connection) -> list[VolumeCoverage]:
    """Per-volume counts, including how many files are not hashed yet and how many assets live only there."""
    only_here = {
        r["vid"]: (r["n"], r["bytes"])
        for r in conn.execute(
            """
            SELECT s.vid, COUNT(*) AS n, COALESCE(SUM(a.size), 0) AS bytes
            FROM (SELECT asset_id, MIN(volume_id) AS vid FROM instances
                  WHERE state = 'verified' AND asset_id IS NOT NULL
                  GROUP BY asset_id HAVING COUNT(DISTINCT volume_id) = 1) s
            JOIN assets a ON a.asset_id = s.asset_id
            GROUP BY s.vid
            """
        )
    }
    out = []
    for v in conn.execute("SELECT volume_id, label, role FROM volumes ORDER BY label"):
        c = conn.execute(
            """
            SELECT SUM(state != 'missing') AS files,
                   SUM(state = 'indexed' AND asset_id IS NULL) AS unhashed,
                   SUM(state = 'mismatch') AS mismatch
            FROM instances WHERE volume_id = ?
            """,
            (v["volume_id"],),
        ).fetchone()
        n, nbytes = only_here.get(v["volume_id"], (0, 0))
        out.append(VolumeCoverage(v["volume_id"], v["label"], v["role"], c["files"] or 0, c["unhashed"] or 0,
                                  c["mismatch"] or 0, n, nbytes))
    return out


def default_report_path(home: Path, *, now: datetime | None = None) -> Path:
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return home / "reports" / f"duplicates-{stamp}.md"


def check_output_path(out: Path) -> Path:
    """Refuse a report destination on a mounted drive: agents and the engine never write to /Volumes (rule 2)."""
    for candidate in (Path(out).expanduser().absolute(), Path(out).expanduser().resolve()):
        if candidate == VOLUMES_ROOT or VOLUMES_ROOT in candidate.parents:
            raise ValueError(f"the report must not be written under {VOLUMES_ROOT}: {out}")
    return Path(out)


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def write_markdown(clusters: list[Cluster], out: Path, *, summaries: list[VolumeCoverage] | None = None,
                   unreadable: list[UnreadableFile] | None = None) -> None:
    """Write the report to `out` (parent directories are created). Raises ValueError for a path under /Volumes."""
    out = check_output_path(out)
    reclaimable = sum(c.reclaimable for c in clusters)
    copies = sum(len(c.instances) for c in clusters)
    lr_clusters = sum(1 for c in clusters if any(i.lr_referenced for i in c.instances))
    lines = [
        "# Duplicate and coverage report",
        "",
        f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}. Identity is the full SHA-256; only hashed files "
        "are compared. Nothing was changed on any drive.",
        "",
        "## Summary",
        "",
        f"- Duplicate clusters: {len(clusters)} ({copies} files)",
        f"- Reclaimable: {human_bytes(reclaimable)} ({reclaimable} bytes)",
        f"- Clusters with a copy a Lightroom catalog references: {lr_clusters} "
        "(only known for catalogs run through `lrcat mark`)",
        "",
    ]
    if summaries is not None:
        lines += [
            "## Coverage per volume",
            "",
            "| Volume | Role | Files | Not hashed yet | Unreadable | Only on this volume |",
            "|---|---|---:|---:|---:|---:|",
        ]
        for s in summaries:
            lines.append(f"| {_cell(s.label)} | {s.role} | {s.files} | {s.unhashed} | {s.mismatch} | "
                         f"{s.only_here_files} ({human_bytes(s.only_here_bytes)}) |")
        lines += ["", "Files that are not hashed yet cannot appear in a cluster. Run `photovault hash` to include them.", ""]
    if unreadable:
        no_copy = sum(1 for u in unreadable if u.rescue == NO_COPY)
        lines += [
            "## Unreadable files (I/O errors)",
            "",
            f"{len(unreadable)} files could not be read in full; {no_copy} have no known copy on another drive. "
            "A copy listed here is a hint, not a verified match. Retry with `photovault hash <mount> "
            "--retry-unreadable`.",
            "",
            "| Volume | File | Size | Copy elsewhere |",
            "|---|---|---:|---|",
        ]
        for u in unreadable:
            lines.append(f"| {_cell(u.volume_label)} | `{_cell(u.rel_path)}` | {human_bytes(u.size)} | "
                         f"{_cell(u.rescue)} |")
        lines.append("")
    lines += ["## Clusters", ""]
    if not clusters:
        lines.append("No duplicates found.")
    for n, c in enumerate(clusters, 1):
        lines += [
            f"### {n}. {human_bytes(c.size)} x {len(c.instances)} (reclaimable {human_bytes(c.reclaimable)})",
            "",
            f"sha256 `{c.sha256[:12]}`",
            "",
        ]
        for i in c.instances:
            lr = " (Lightroom)" if i.lr_referenced else ""
            lines.append(f"- {i.volume_label} [{i.volume_role}]: `{i.rel_path}`{lr}")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


__all__ = ["NO_COPY", "Cluster", "ClusterInstance", "UnreadableFile", "VolumeCoverage", "coverage",
           "default_report_path", "duplicates", "unreadable", "write_markdown"]
