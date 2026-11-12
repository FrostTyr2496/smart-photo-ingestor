"""live-1: the live-device harness. Uses only tmp_path and fake registries; never touches /Volumes."""

from __future__ import annotations

import builtins
import io
import os
import re
from pathlib import Path

import pytest

import photovault.live_harness as lh
from photovault.live_harness import (
    LiveRegistryError,
    LiveSource,
    SourceUnavailable,
    get_source,
    iter_all,
    iter_entries,
    load_registry,
    open_rb,
    registry_path,
)

pytest_plugins = ["pytester"]


def _registry(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "live_sources.toml"
    path.write_text(body, encoding="utf-8")
    return path


@pytest.fixture
def mount(tmp_path: Path) -> Path:
    root = tmp_path / "fake_card"
    (root / "DCIM" / "100TEST").mkdir(parents=True)
    (root / "DCIM" / "100TEST" / "DSC_0001.NEF").write_bytes(b"raw-bytes")
    (root / "DCIM" / "100TEST" / "DSC_0001.JPG").write_bytes(b"jpg-bytes")
    (root / "notes.txt").write_bytes(b"ignored kind")
    return root


# --- registry ---------------------------------------------------------------------------------


def test_missing_registry_is_empty(tmp_path: Path) -> None:
    assert load_registry(tmp_path / "nope.toml") == []


def test_registry_parses_and_sorts(tmp_path: Path) -> None:
    reg = _registry(
        tmp_path,
        '[sources.zed]\npath = "/mnt/zed"\nrole = "LEGACY"\n\n[sources.alpha]\npath = "/mnt/alpha"\nrole = "CARD"\n',
    )
    sources = load_registry(reg)
    assert [(s.name, s.path, s.role) for s in sources] == [
        ("alpha", Path("/mnt/alpha"), "CARD"),
        ("zed", Path("/mnt/zed"), "LEGACY"),
    ]


def test_committed_example_registry_is_valid() -> None:
    example = Path(__file__).resolve().parent.parent / "docs" / "live_sources.example.toml"
    sources = load_registry(example)
    assert {s.role for s in sources} <= {"CARD", "WORKING", "MASTER", "LEGACY"}
    assert sources and all(s.path.as_posix().startswith("/Volumes/EXAMPLE_") for s in sources)


@pytest.mark.parametrize(
    ("body", "needle"),
    [
        ("not toml [[[", "not valid TOML"),
        ('[extra]\nx = 1\n', "unknown top-level key 'extra'"),
        ("sources = 3\n", "must be a table of tables"),
        ('[sources.a]\nrole = "CARD"\n', "'path' must be a non-empty string"),
        ('[sources.a]\npath = "rel/path"\nrole = "CARD"\n', "must be absolute"),
        ('[sources.a]\npath = "/x"\nrole = "BOSS"\n', "'role' must be one of"),
        ('[sources.a]\npath = "/x"\n', "'role' must be one of"),
        ('[sources.a]\npath = "/x"\nrole = "CARD"\nmode = "rw"\n', "unknown key 'mode'"),
    ],
)
def test_bad_registry_raises_with_a_specific_message(tmp_path: Path, body: str, needle: str) -> None:
    with pytest.raises(LiveRegistryError, match=re.escape(needle)):
        load_registry(_registry(tmp_path, body))


def test_registry_path_honours_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv(lh.REGISTRY_ENV, raising=False)
    assert registry_path() == lh.DEFAULT_REGISTRY
    assert lh.DEFAULT_REGISTRY.parts[-2:] == ("private", "live_sources.toml")
    monkeypatch.setenv(lh.REGISTRY_ENV, str(tmp_path / "other.toml"))
    assert registry_path() == tmp_path / "other.toml"


def test_get_source_names_the_known_sources_on_a_miss() -> None:
    sources = [LiveSource("a", Path("/x"), "CARD")]
    assert get_source("a", sources).name == "a"
    with pytest.raises(KeyError, match="known: \\['a'\\]"):
        get_source("b", sources)


# --- walking ----------------------------------------------------------------------------------


def test_iter_entries_matches_scan_walk(mount: Path) -> None:
    source = LiveSource("card", mount, "CARD")
    entries = list(iter_entries(source))
    assert [e.rel_path for e in entries] == ["DCIM/100TEST/DSC_0001.JPG", "DCIM/100TEST/DSC_0001.NEF"]
    assert {e.kind for e in entries} == {"jpeg", "raw"}
    with_ignored = list(iter_entries(source, include_ignored=True))
    assert "notes.txt" in [e.rel_path for e in with_ignored]


def test_unavailable_source_raises_or_is_skipped(tmp_path: Path, mount: Path) -> None:
    gone = LiveSource("gone", tmp_path / "unplugged", "CARD")
    here = LiveSource("here", mount, "WORKING")
    assert gone.available is False and here.available is True
    with pytest.raises(SourceUnavailable, match="not mounted"):
        list(iter_entries(gone))
    pairs = list(iter_all([gone, here]))
    assert {s.name for s, _ in pairs} == {"here"}
    with pytest.raises(SourceUnavailable):
        list(iter_all([gone, here], skip_unavailable=False))


def test_a_file_path_is_not_a_mount(tmp_path: Path) -> None:
    file_path = tmp_path / "just_a_file"
    file_path.write_bytes(b"x")
    assert LiveSource("f", file_path, "CARD").available is False


# --- read-only guarantee ----------------------------------------------------------------------


def _is_write_mode(mode: object) -> bool:
    return isinstance(mode, str) and any(ch in mode for ch in "wax+")


def test_harness_never_opens_a_file_for_writing(
    monkeypatch: pytest.MonkeyPatch, mount: Path, tmp_path: Path
) -> None:
    reg = _registry(tmp_path, f'[sources.card]\npath = "{mount}"\nrole = "CARD"\n')
    seen: list[tuple[str, object]] = []
    real_open, real_io_open, real_os_open = builtins.open, io.open, os.open

    def spy_open(file, mode="r", *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        seen.append(("open", mode))
        return real_open(file, mode, *args, **kwargs)

    def spy_io_open(file, mode="r", *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        seen.append(("io.open", mode))
        return real_io_open(file, mode, *args, **kwargs)

    def spy_os_open(path, flags, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003
        seen.append(("os.open", flags))
        return real_os_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_io_open)
    monkeypatch.setattr(os, "open", spy_os_open)

    for source in load_registry(reg):
        for entry in iter_entries(source, include_ignored=True):
            with open_rb(entry) as handle:
                assert handle.read()

    assert seen, "the spy saw no opens, so the test proves nothing"
    for kind, mode in seen:
        if kind == "os.open":
            write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND
            assert not (mode & write_flags), f"os.open with write flags {mode:#x}"
        else:
            assert not _is_write_mode(mode), f"{kind} with write mode {mode!r}"


def test_open_rb_is_binary_read_and_rejects_writes(mount: Path) -> None:
    entry = next(iter(iter_entries(LiveSource("card", mount, "CARD"))))
    with open_rb(entry) as handle:
        assert handle.mode == "rb"
        with pytest.raises(io.UnsupportedOperation):
            handle.write(b"nope")
    assert entry.abs_path.read_bytes() in (b"raw-bytes", b"jpg-bytes")


def test_harness_leaves_the_mount_untouched(mount: Path) -> None:
    def snapshot() -> dict[str, tuple[int, int, bytes]]:
        return {
            p.relative_to(mount).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns, p.read_bytes())
            for p in sorted(mount.rglob("*"))
            if p.is_file()
        }

    before = snapshot()
    dirs_before = sorted(p.relative_to(mount).as_posix() for p in mount.rglob("*") if p.is_dir())
    for _source, entry in iter_all([LiveSource("card", mount, "CARD")], include_ignored=True):
        with open_rb(entry) as handle:
            handle.read()
    assert snapshot() == before
    assert sorted(p.relative_to(mount).as_posix() for p in mount.rglob("*") if p.is_dir()) == dirs_before


def test_module_source_has_no_write_apis() -> None:
    source = Path(lh.__file__).read_text(encoding="utf-8")
    forbidden = [
        r"\.write_bytes\(", r"\.write_text\(", r"\.mkdir\(", r"\.touch\(", r"\.unlink\(", r"\.rename\(",
        r"\.replace\(", r"\.rmdir\(", r"\bos\.(remove|rename|unlink|mkdir|makedirs|rmdir|replace|"
        r"chmod|utime|symlink|link|truncate)\b", r"\bshutil\.", r"\bsubprocess\b",
        r"""\bopen\([^)]*["'][^"']*[wax+][^"']*["']""", r"\.open\(",
    ]
    for pattern in forbidden:
        assert not re.search(pattern, source), f"live_harness.py matches forbidden pattern {pattern!r}"


# --- the `live` marker and its fixtures (run in a nested pytest session) ---------------------

LIVE_TEST = """
import pytest
from photovault.live_harness import iter_entries

@pytest.mark.live
def test_reads_a_card(live_source):
    source = live_source("card")
    assert [e.rel_path for e in iter_entries(source)]
"""


@pytest.fixture
def nested(pytester: pytest.Pytester) -> pytest.Pytester:
    pytester.makeconftest((Path(__file__).parent / "conftest.py").read_text(encoding="utf-8"))
    pytester.makeini("[pytest]\nmarkers =\n    live: live tests\n")
    pytester.makepyfile(test_live=LIVE_TEST)
    return pytester


def _skip_reason(result: pytest.RunResult) -> str:
    result.assert_outcomes(skipped=1)
    return result.stdout.str()


def test_live_tests_skip_without_dash_m_live(
    nested: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, mount: Path, tmp_path: Path
) -> None:
    reg = _registry(tmp_path, f'[sources.card]\npath = "{mount}"\nrole = "CARD"\n')
    monkeypatch.setenv(lh.REGISTRY_ENV, str(reg))
    out = _skip_reason(nested.runpytest_inprocess("-rs"))
    assert "run only with `-m live`" in out


def test_live_tests_skip_when_no_registry(
    nested: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(lh.REGISTRY_ENV, str(tmp_path / "absent.toml"))
    out = _skip_reason(nested.runpytest_inprocess("-m", "live", "-rs"))
    assert "no live registry" in out


def test_live_tests_skip_when_the_mount_is_absent(
    nested: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    reg = _registry(tmp_path, f'[sources.card]\npath = "{tmp_path / "unplugged"}"\nrole = "CARD"\n')
    monkeypatch.setenv(lh.REGISTRY_ENV, str(reg))
    out = _skip_reason(nested.runpytest_inprocess("-m", "live", "-rs"))
    assert "is not mounted" in out


def test_live_tests_skip_when_the_source_name_is_unknown(
    nested: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, mount: Path, tmp_path: Path
) -> None:
    reg = _registry(tmp_path, f'[sources.other]\npath = "{mount}"\nrole = "CARD"\n')
    monkeypatch.setenv(lh.REGISTRY_ENV, str(reg))
    out = _skip_reason(nested.runpytest_inprocess("-m", "live", "-rs"))
    assert "no live source named 'card'" in out


def test_live_tests_run_when_registry_and_mount_exist(
    nested: pytest.Pytester, monkeypatch: pytest.MonkeyPatch, mount: Path, tmp_path: Path
) -> None:
    reg = _registry(tmp_path, f'[sources.card]\npath = "{mount}"\nrole = "CARD"\n')
    monkeypatch.setenv(lh.REGISTRY_ENV, str(reg))
    nested.runpytest_inprocess("-m", "live").assert_outcomes(passed=1)
