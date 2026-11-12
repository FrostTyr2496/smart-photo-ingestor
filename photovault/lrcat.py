"""Read-only Lightroom Classic catalog reader (idx-3).

Lists the files a `.lrcat` references so the catalog can flag them (`instances.lr_referenced`) and nothing
moves or renames them (vault-rules 7). The `.lrcat` is opened read-only and immutable, and never while
Lightroom may have it open: a `<lrcat>.lock` file or a running Lightroom process refuses the read. A lock
file alone is not proof of an open catalog (a stale one was seen on the legacy drive), but we cannot tell the
two apart, so either one refuses and the user decides.

The table and column names come from knowledge of Lightroom Classic and are unverified against real catalogs
until idx-5. A mismatch raises `LrcatSchemaError`: stop and report, don't guess.
"""

from __future__ import annotations

import posixpath
import sqlite3
import subprocess
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from photovault.catalog import repo

LIGHTROOM_PROCESSES = ("Adobe Lightroom Classic", "Adobe Lightroom")

_QUERY = """
SELECT r.absolutePath, d.pathFromRoot, f.baseName, f.extension, f.sidecarExtensions
FROM AgLibraryFile AS f
JOIN AgLibraryFolder AS d ON f.folder = d.id_local
JOIN AgLibraryRootFolder AS r ON d.rootFolder = r.id_local
"""


class CatalogInUse(RuntimeError):
    """The Lightroom catalog may be open (a lock file exists or Lightroom is running); it was not read."""


class LrcatSchemaError(RuntimeError):
    """The file is not a Lightroom catalog with the expected tables and columns."""


def lightroom_running(run: Callable = subprocess.run) -> bool:
    """True when a Lightroom process is running. Matches the exact process name, never a command line."""
    for name in LIGHTROOM_PROCESSES:
        try:
            done = run(["pgrep", "-x", name], capture_output=True, text=True, check=False)
        except OSError as exc:  # pgrep missing: we cannot tell, so refuse rather than read a possibly open catalog
            raise CatalogInUse(f"cannot check whether Lightroom is running ({exc}); close it and retry") from exc
        if done.returncode == 0:
            return True
    return False


def _join(root: str, folder: str | None, name: str) -> str:
    return posixpath.join(root, (folder or "").lstrip("/"), name)


def referenced_paths(lrcat: Path, *, is_running: Callable[[], bool] | None = None) -> set[str]:
    """Absolute paths of every file (and sidecar) the catalog references. Never writes to the catalog."""
    lrcat = Path(lrcat).resolve()
    lock = lrcat.with_name(lrcat.name + ".lock")
    if lock.exists():
        raise CatalogInUse(
            f"{lock} exists, so Lightroom may have this catalog open. Close Lightroom and retry; if Lightroom is "
            "not running the lock is stale, and you can read a copy of the catalog instead"
        )
    if (is_running or lightroom_running)():
        raise CatalogInUse("Lightroom is running. Close it and retry")
    if not lrcat.is_file():
        raise FileNotFoundError(f"no such catalog: {lrcat}")
    uri = f"file:{quote(str(lrcat))}?mode=ro&immutable=1"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise LrcatSchemaError(f"cannot open {lrcat} as a SQLite catalog: {exc}") from exc
    try:
        rows = conn.execute(_QUERY).fetchall()
    except sqlite3.Error as exc:
        raise LrcatSchemaError(
            f"{lrcat.name} does not look like a Lightroom Classic catalog (expected AgLibraryFile, AgLibraryFolder "
            f"and AgLibraryRootFolder): {exc}"
        ) from exc
    finally:
        conn.close()
    paths: set[str] = set()
    for root, folder, base, ext, sidecars in rows:
        if not root or not base:
            continue
        stem = _join(root, folder, base)
        paths.add(f"{stem}.{ext}" if ext else stem)
        for s in (sidecars or "").split(","):
            s = s.strip().lstrip(".")
            if s:
                paths.add(f"{stem}.{s}")
    return paths


def keys_on_volume(mount: Path, paths: set[str]) -> set[str]:
    """`rel_path_key` for each path under `mount` (paths on other drives are left out)."""
    prefix = repo.path_key(posixpath.join(str(mount).rstrip("/"), ""))
    keys = (repo.path_key(p) for p in paths)
    return {k[len(prefix):] for k in keys if k.startswith(prefix) and len(k) > len(prefix)}


def mark_lr_referenced(conn: sqlite3.Connection, volume_id: str, mount: Path, paths: set[str]) -> int:
    """Set `instances.lr_referenced = 1` for catalogued instances on this volume; returns how many changed.

    Additive: it never clears a flag, because more than one Lightroom catalog may reference a drive. Paths are
    matched case-insensitively and NFC-normalised, like `rel_path_key`.
    """
    keys = sorted(keys_on_volume(mount, paths))
    changed = 0
    with repo.transaction(conn):
        for i in range(0, len(keys), 500):
            chunk = keys[i:i + 500]
            marks = ",".join("?" * len(chunk))
            cur = conn.execute(
                f"UPDATE instances SET lr_referenced = 1 WHERE volume_id = ? AND lr_referenced = 0 "
                f"AND rel_path_key IN ({marks})",
                [volume_id, *chunk],
            )
            changed += cur.rowcount
    return changed
