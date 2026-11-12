"""Shared pytest hooks.

`@pytest.mark.live` tests read real mounted drives (read-only). They run only when you pass
`-m live` explicitly and a live registry exists, so a plain `uv run pytest -q` never touches a mount.
Use the `live_sources` and `live_source` fixtures: they skip when the registry or the mount is absent.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from photovault.live_harness import LiveSource, get_source, load_registry, registry_path


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if "live" in (config.getoption("-m") or ""):
        return
    skip = pytest.mark.skip(reason="live tests run only with `-m live`")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def live_sources() -> list[LiveSource]:
    """Every source in the live registry; skips the test when there is no registry or it is empty."""
    sources = load_registry()
    if not sources:
        pytest.skip(f"no live registry at {registry_path()}")
    return sources


@pytest.fixture
def live_source(live_sources: list[LiveSource]) -> Callable[[str], LiveSource]:
    """Return a factory: `live_source("name")` gives that source, or skips when it is unplugged."""

    def pick(name: str) -> LiveSource:
        try:
            source = get_source(name, live_sources)
        except KeyError as exc:
            pytest.skip(str(exc))
        if not source.available:
            pytest.skip(f"live source {name!r} is not mounted ({source.path})")
        return source

    return pick
