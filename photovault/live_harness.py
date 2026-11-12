"""Live-device test harness (readiness spec, live-1).

Reads a registry of plugged-in sources (`name -> mount path`, with a role) and yields
`scan.walk` entries for each, so tests can read live files in place instead of keeping
copies in the local corpus.

Read-only by construction: this module opens files `"rb"` only and never writes, renames
or deletes anything. Registry format (TOML), one table per source::

    [sources.card_a]
    path = "/Volumes/EXAMPLE_CARD"
    role = "CARD"

The real registry is `docs/private/live_sources.toml` (gitignored). The committed placeholder
is `docs/live_sources.example.toml`. `$PHOTOVAULT_LIVE_SOURCES` overrides the registry path.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from photovault.scan import FileEntry, walk
from photovault.volumes import ROLES

REGISTRY_ENV = "PHOTOVAULT_LIVE_SOURCES"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_REGISTRY = REPO_ROOT / "docs" / "private" / "live_sources.toml"


class LiveRegistryError(ValueError):
    """The registry file is malformed. The message names the bad source or key."""


class SourceUnavailable(RuntimeError):
    """A registered source's mount path is absent or not a directory."""


@dataclass(frozen=True)
class LiveSource:
    name: str
    path: Path
    role: str

    @property
    def available(self) -> bool:
        return self.path.is_dir()


def registry_path() -> Path:
    """The registry file to use: `$PHOTOVAULT_LIVE_SOURCES`, else `docs/private/live_sources.toml`."""
    override = os.environ.get(REGISTRY_ENV)
    return Path(override) if override else DEFAULT_REGISTRY


def load_registry(path: Path | None = None) -> list[LiveSource]:
    """Parse the registry into sources sorted by name.

    Returns `[]` when the file does not exist (no registry means nothing to test live).
    Raises `LiveRegistryError` for bad TOML, a missing or relative `path`, an unknown `role`,
    or a top-level key other than `sources`.
    """
    reg = path if path is not None else registry_path()
    try:
        raw = reg.read_bytes()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise LiveRegistryError(f"cannot read live registry {reg}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise LiveRegistryError(f"live registry {reg} is not valid TOML: {exc}") from exc

    unknown = sorted(set(data) - {"sources"})
    if unknown:
        raise LiveRegistryError(f"live registry {reg}: unknown top-level key {unknown[0]!r}")
    table = data.get("sources", {})
    if not isinstance(table, dict):
        raise LiveRegistryError(f"live registry {reg}: 'sources' must be a table of tables")

    sources: list[LiveSource] = []
    for name, body in table.items():
        if not isinstance(body, dict):
            raise LiveRegistryError(f"source {name!r}: expected a table with 'path' and 'role'")
        extra = sorted(set(body) - {"path", "role"})
        if extra:
            raise LiveRegistryError(f"source {name!r}: unknown key {extra[0]!r}")
        raw_path = body.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            raise LiveRegistryError(f"source {name!r}: 'path' must be a non-empty string")
        mount = Path(raw_path)
        if not mount.is_absolute():
            raise LiveRegistryError(f"source {name!r}: 'path' must be absolute, got {raw_path!r}")
        role = body.get("role")
        if role not in ROLES:
            raise LiveRegistryError(f"source {name!r}: 'role' must be one of {sorted(ROLES)}, got {role!r}")
        sources.append(LiveSource(name=name, path=mount, role=role))
    return sorted(sources, key=lambda s: s.name)


def get_source(name: str, sources: Iterable[LiveSource]) -> LiveSource:
    """Return the source called `name`, or raise `KeyError` listing the known names."""
    by_name = {s.name: s for s in sources}
    try:
        return by_name[name]
    except KeyError:
        raise KeyError(f"no live source named {name!r}; known: {sorted(by_name)}") from None


def iter_entries(source: LiveSource, *, include_ignored: bool = False) -> Iterator[FileEntry]:
    """Yield `scan.walk` entries for one source, in sorted `rel_path` order.

    Raises `SourceUnavailable` if the mount path is not a directory (unplugged drive).
    """
    if not source.available:
        raise SourceUnavailable(f"live source {source.name!r}: {source.path} is not mounted")
    yield from walk(source.path, include_ignored=include_ignored)


def iter_all(
    sources: Iterable[LiveSource], *, include_ignored: bool = False, skip_unavailable: bool = True
) -> Iterator[tuple[LiveSource, FileEntry]]:
    """Yield `(source, entry)` for every source. Unplugged sources are skipped unless told otherwise."""
    for source in sources:
        if not source.available and skip_unavailable:
            continue
        for entry in iter_entries(source, include_ignored=include_ignored):
            yield source, entry


def open_rb(entry: FileEntry) -> BinaryIO:
    """Open a live file for reading. The mode is fixed to `"rb"`; there is no way to ask for another."""
    return open(entry.abs_path, "rb")
