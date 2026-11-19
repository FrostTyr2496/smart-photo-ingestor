"""Test card mode (tc-1, D-111): pretend one card's files have no copy on some drives.

Stored in the catalog `settings` table under `test_card` (no schema change). For an asset that has an instance (any
state) on the test card, `verified` instances on the hidden volumes are ignored by every backup judgement. It only
ever errs toward caution: more red, more copying, less clearing. Nothing on any drive is touched.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from dataclasses import dataclass
from datetime import UTC, datetime

from photovault import volumes
from photovault.catalog import repo

KEY = "test_card"


@dataclass(frozen=True)
class TestCard:
    __test__ = False  # not a pytest class
    card_volume_id: str
    hidden_volume_ids: tuple[str, ...]
    marked_at: str


def load(conn: sqlite3.Connection) -> TestCard | None:
    raw = repo.get_setting(conn, KEY)
    if not raw:
        return None
    try:
        d = json.loads(raw)
        return TestCard(str(d["card_volume_id"]), tuple(str(x) for x in d["hidden_volume_ids"]), str(d.get("marked_at", "")))
    except (ValueError, KeyError, TypeError):
        return None


def mark(conn: sqlite3.Connection, card_volume_id: str, hidden_volume_ids: list[str]) -> TestCard:
    card = repo.get_volume(conn, card_volume_id)
    if card is None or card["role"] != "CARD":
        raise ValueError(f"{card_volume_id} is not a card in this catalog")
    if not hidden_volume_ids:
        raise ValueError("name at least one drive to hide")
    for vid in hidden_volume_ids:
        v = repo.get_volume(conn, vid)
        if v is None:
            raise ValueError(f"{vid} is not a volume in this catalog")
        if v["role"] == "CARD":
            raise ValueError(f"{v['label']} is a card; only drives can be hidden")
    tc = TestCard(card_volume_id, tuple(dict.fromkeys(hidden_volume_ids)), datetime.now(UTC).isoformat(timespec="seconds"))
    with repo.transaction(conn):
        repo.set_setting(conn, KEY, json.dumps({"card_volume_id": tc.card_volume_id,
                                                "hidden_volume_ids": list(tc.hidden_volume_ids),
                                                "marked_at": tc.marked_at}))
    return tc


def clear(conn: sqlite3.Connection) -> bool:
    had = load(conn) is not None
    with repo.transaction(conn):
        conn.execute("DELETE FROM settings WHERE key = ?", (KEY,))
    return had


def hidden_for_card(conn: sqlite3.Connection, card_volume_id: str | None) -> frozenset[str]:
    """Hidden volume ids for files of this card (empty unless it is the test card)."""
    tc = load(conn)
    return frozenset(tc.hidden_volume_ids) if tc and card_volume_id == tc.card_volume_id else frozenset()


def hidden_for_asset(conn: sqlite3.Connection, asset_id: int) -> frozenset[str]:
    """Hidden volume ids that apply to this asset (it has an instance on the test card)."""
    tc = load(conn)
    if tc is None:
        return frozenset()
    row = conn.execute("SELECT 1 FROM instances WHERE asset_id = ? AND volume_id = ? LIMIT 1",
                       (asset_id, tc.card_volume_id)).fetchone()
    return frozenset(tc.hidden_volume_ids) if row else frozenset()


def sql_hide(conn: sqlite3.Connection, alias: str) -> tuple[str, list[str]]:
    """``(" AND NOT (...)", params)`` to append to a query over copy instances `alias` (needs `alias.asset_id` and
    `alias.volume_id`): drops copies on hidden volumes whose asset is on the test card. ``("", [])`` when none."""
    tc = load(conn)
    if tc is None:
        return "", []
    q = ",".join("?" * len(tc.hidden_volume_ids))
    return (f" AND NOT ({alias}.volume_id IN ({q}) AND EXISTS ("
            f"SELECT 1 FROM instances tcx WHERE tcx.asset_id = {alias}.asset_id AND tcx.volume_id = ?))",
            [*tc.hidden_volume_ids, tc.card_volume_id])


def resolve(conn: sqlite3.Connection, ref: str) -> str:
    """A volume id from a volume id, a mount path, ``card:LABEL`` or a plain label. Reads the catalog (and probes a
    mount path read-only); raises ValueError when nothing or several volumes match."""
    if repo.get_volume(conn, ref) is not None:
        return ref
    if ref.startswith("/") and Path(ref).is_dir():
        p = volumes.probe(Path(ref))
        row = repo.find_volume(conn, marker_id=p.marker_id, da_volume_uuid=p.da_volume_uuid)
        if row is None:
            raise ValueError(f"{ref} is not known to the catalog")
        return row["volume_id"]
    vols = repo.list_volumes(conn)
    hits = [v for v in vols if v["label"] == ref]
    if not hits and ref.lower().startswith("card:"):
        hits = [v for v in vols if v["role"] == "CARD" and v["label"] in (ref[5:], ref)]
    if len(hits) != 1:
        raise ValueError(f"{ref!r} matches {len(hits)} volumes; use a mount path or volume id")
    return hits[0]["volume_id"]


def describe(conn: sqlite3.Connection) -> dict:
    tc = load(conn)
    if tc is None:
        return {"test_card": None}
    def label(vid: str) -> str:
        v = repo.get_volume(conn, vid)
        return v["label"] if v else vid
    return {"test_card": {"card_volume_id": tc.card_volume_id, "card": label(tc.card_volume_id),
                          "hidden": [{"volume_id": h, "label": label(h)} for h in tc.hidden_volume_ids],
                          "marked_at": tc.marked_at}}
