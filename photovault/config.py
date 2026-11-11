"""Configuration: frozen dataclasses, the TOML loader and its validation. See system-design §3, §6, §9.

Sections: [ingest] [naming] [policy] [bodies] [performance]. Anything else at the top level is an error.
Home is `$PHOTOVAULT_HOME`, or else `~/Library/Application Support/PhotoVault`.
"""

from __future__ import annotations

import os
import string
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROLES = ("WORKING", "MASTER", "LEGACY")
SECTIONS = ("ingest", "naming", "policy", "bodies", "performance")

DEFAULT_BODIES: dict[str, str] = {
    "NIKON Z 6": "Z6",
    "NIKON Z 6_2": "Z6II",
    "NIKON Z 8": "Z8",
    "FC3411": "AIR2S",
    "iPhone 14 Pro": "IP14P",
    "HERO10 Black": "HERO10",
}


class ConfigError(ValueError):
    """A config file or value is invalid. The message names the bad key."""


@dataclass(frozen=True)
class IngestConfig:
    working_subdir: str = "Photography/Imports"


@dataclass(frozen=True)
class NamingConfig:
    template: str = "M{num:04d}_{date}-{time}-{subsec}_{body}_{ident}"
    day_boundary_hour: int = 12


@dataclass(frozen=True)
class PolicyConfig:
    required: dict[str, int] = field(default_factory=lambda: {"WORKING": 1})


@dataclass(frozen=True)
class Config:
    home: Path
    catalog_path: Path
    ingest: IngestConfig
    naming: NamingConfig
    policy: PolicyConfig
    bodies: dict[str, str]
    exiftool_workers: int = 4


def default_home() -> Path:
    """`$PHOTOVAULT_HOME` if set, else `~/Library/Application Support/PhotoVault`."""
    env = os.environ.get("PHOTOVAULT_HOME")
    if env:
        return Path(env).expanduser()
    return Path.home() / "Library" / "Application Support" / "PhotoVault"


def _table(section: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"[{section}] must be a table")
    return value


def _check_keys(section: str, table: dict[str, Any], allowed: tuple[str, ...]) -> None:
    for key in table:
        if key not in allowed:
            raise ConfigError(f"unknown key [{section}] {key}")


def _int(section: str, key: str, value: Any, lo: int, hi: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"[{section}] {key} must be an integer")
    if not lo <= value <= hi:
        raise ConfigError(f"[{section}] {key} must be between {lo} and {hi}, got {value}")
    return value


def _str(section: str, key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ConfigError(f"[{section}] {key} must be a string")
    return value


def _parse_ingest(raw: dict[str, Any]) -> IngestConfig:
    table = _table("ingest", raw.get("ingest", {}))
    _check_keys("ingest", table, ("working_subdir",))
    if "working_subdir" not in table:
        return IngestConfig()
    return IngestConfig(working_subdir=_str("ingest", "working_subdir", table["working_subdir"]))


def _parse_naming(raw: dict[str, Any]) -> NamingConfig:
    table = _table("naming", raw.get("naming", {}))
    _check_keys("naming", table, ("template", "day_boundary_hour"))
    defaults = NamingConfig()
    template = _str("naming", "template", table.get("template", defaults.template))
    fields = {name for _, name, _, _ in string.Formatter().parse(template) if name is not None}
    for required in ("body", "ident"):
        if required not in fields:
            raise ConfigError(f"[naming] template must contain {{{required}}}")
    hour = _int("naming", "day_boundary_hour", table.get("day_boundary_hour", defaults.day_boundary_hour), 0, 23)
    return NamingConfig(template=template, day_boundary_hour=hour)


def _parse_policy(raw: dict[str, Any]) -> PolicyConfig:
    table = _table("policy", raw.get("policy", {}))
    _check_keys("policy", table, ("required",))
    if "required" not in table:
        return PolicyConfig()
    required_raw = _table("policy", table["required"])
    required: dict[str, int] = {}
    for role, count in required_raw.items():
        if role not in ROLES:
            raise ConfigError(f"[policy] required: unknown role {role!r} (use one of {', '.join(ROLES)})")
        required[role] = _int("policy", f"required.{role}", count, 1, 2**31 - 1)
    return PolicyConfig(required=required)


def _parse_bodies(raw: dict[str, Any]) -> dict[str, str]:
    table = _table("bodies", raw.get("bodies", {}))
    bodies = dict(DEFAULT_BODIES)
    for model, nickname in table.items():
        bodies[model] = _str("bodies", model, nickname)
    return bodies


def _parse_performance(raw: dict[str, Any]) -> int:
    table = _table("performance", raw.get("performance", {}))
    _check_keys("performance", table, ("exiftool_workers",))
    return _int("performance", "exiftool_workers", table.get("exiftool_workers", 4), 1, 8)


def load_config(path: Path | None = None) -> Config:
    """Load config. `path=None` uses `<home>/config.toml` when it exists, else the defaults.

    An explicit `path` must exist. Raises `ConfigError` on bad TOML, unknown sections or keys, or bad values.
    """
    home = default_home()
    if path is None:
        candidate = home / "config.toml"
        path = candidate if candidate.exists() else None

    raw: dict[str, Any] = {}
    if path is not None:
        try:
            with path.open("rb") as fh:
                raw = tomllib.load(fh)
        except FileNotFoundError as exc:
            raise ConfigError(f"config file not found: {path}") from exc
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"config file is not valid TOML ({path}): {exc}") from exc

    for section in raw:
        if section not in SECTIONS:
            raise ConfigError(f"unknown config section [{section}]")

    return Config(
        home=home,
        catalog_path=home / "catalog.sqlite",
        ingest=_parse_ingest(raw),
        naming=_parse_naming(raw),
        policy=_parse_policy(raw),
        bodies=_parse_bodies(raw),
        exiftool_workers=_parse_performance(raw),
    )
