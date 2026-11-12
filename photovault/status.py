"""Card status (ing-8): is every file on a card safely copied? Read-only. See system-design §9."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from photovault import volumes
from photovault.catalog import repo
from photovault.config import Config
from photovault.scan import walk

MTIME_TOLERANCE_NS = 2_000_000_000  # exFAT has 2 s mtime resolution (vault-rules 8)


@dataclass
class CardStatus:
    volume_id: str | None
    state: str
    total: int = 0
    safe: int = 0
    unknown: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    short: list[tuple[str, dict]] = field(default_factory=list)


def card_status(conn: sqlite3.Connection, cfg: Config, mount: Path) -> CardStatus:
    """Green iff every imported-kind file on the card meets `cfg.policy.required`. Writes nothing."""
    p = volumes.probe(Path(mount))
    row = repo.find_volume(conn, da_volume_uuid=p.da_volume_uuid) if p.da_volume_uuid else None
    status = CardStatus(volume_id=row["volume_id"] if row else None, state="red")
    for e in walk(Path(mount)):
        status.total += 1
        inst = repo.get_instance(conn, row["volume_id"], e.rel_path) if row else None
        if inst is None:
            status.unknown.append(e.rel_path)
            continue
        if inst["size"] != e.size or abs(inst["mtime_ns"] - e.mtime_ns) > MTIME_TOLERANCE_NS:
            status.changed.append(e.rel_path)
            continue
        if inst["asset_id"] is None:
            status.unknown.append(e.rel_path)
            continue
        counts = repo.verified_counts_by_role(conn, inst["asset_id"])
        if any(counts.get(role, 0) < need for role, need in cfg.policy.required.items()):
            status.short.append((e.rel_path, counts))
            continue
        status.safe += 1
    status.state = "green" if status.total > 0 and status.safe == status.total else "red"
    return status
