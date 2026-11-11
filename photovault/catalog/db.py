"""SQLite catalog connection, migration runner and snapshot. See system-design.md sections 3 and 5."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from photovault.catalog import migrations


def connect(path: Path) -> sqlite3.Connection:
    """Open the catalog at ``path`` (creating parent dirs) with the PhotoVault PRAGMAs applied.

    Autocommit-style transaction control is left to the caller except in ``migrate``, which manages its
    own transactions, so the connection is opened with ``isolation_level=None``.
    """
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OSError(f"cannot create catalog directory {path.parent}: {exc}") from exc
    try:
        conn = sqlite3.connect(path, isolation_level=None)
    except sqlite3.Error as exc:
        raise sqlite3.OperationalError(f"cannot open catalog at {path}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
        if str(mode).lower() != "wal":
            raise sqlite3.OperationalError(
                f"could not enable WAL journal mode on {path} (got {mode!r}); is it on a network or exFAT volume?"
            )
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA synchronous=FULL")
    except Exception:
        conn.close()
        raise
    return conn


def migrate(conn: sqlite3.Connection) -> int:
    """Apply pending migrations and return ``PRAGMA user_version``.

    Migration *n* runs as ``BEGIN; <sql>; PRAGMA user_version=n; COMMIT;``. On error the transaction is
    rolled back and the error is re-raised.
    """
    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if current > len(migrations.MIGRATIONS):
        raise RuntimeError(
            f"catalog schema version {current} is newer than this engine supports "
            f"({len(migrations.MIGRATIONS)}); upgrade PhotoVault"
        )
    for n in range(current + 1, len(migrations.MIGRATIONS) + 1):
        sql = migrations.MIGRATIONS[n - 1]
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version={n};\nCOMMIT;")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.rollback()
            raise sqlite3.OperationalError(f"catalog migration {n} failed and was rolled back: {exc}") from exc
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def snapshot(conn: sqlite3.Connection, dest: Path) -> None:
    """Write a consistent copy of the catalog to ``dest`` with ``VACUUM INTO``. ``dest`` must not exist."""
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(f"snapshot destination already exists: {dest}")
    try:
        conn.execute("VACUUM INTO ?", (str(dest),))
    except sqlite3.Error as exc:
        raise sqlite3.OperationalError(f"snapshot to {dest} failed: {exc}") from exc
