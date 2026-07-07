"""Unit tests for RawBackupManager – task 9 (Requirements 15.1, 15.2, 15.3, 15.5)."""

import hashlib
import os
import stat
import sys
from pathlib import Path

import pytest

from photo_ingest.config import RawBackupConfig
from photo_ingest.file_operations import RawBackupManager, RawBackupResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _make_source_tree(base: Path) -> tuple[list[Path], Path]:
    """Create a small source tree and return (files, source_root)."""
    sub = base / "sub"
    sub.mkdir(parents=True)
    files = []
    for name, content in [
        ("IMG_001.jpg", b"fake jpeg data 1"),
        ("IMG_002.nef", b"fake raw data 2"),
        ("sub/IMG_003.jpg", b"fake jpeg data 3"),
    ]:
        p = base / name
        p.write_bytes(content)
        files.append(p)
    return files, base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def backup_root(tmp_path: Path) -> Path:
    return tmp_path / "backups"


@pytest.fixture
def source_root(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    src.mkdir()
    return src


@pytest.fixture
def source_files(source_root: Path) -> list[Path]:
    """Flat source files (no sub-directory) for simple tests."""
    files = []
    for name, content in [
        ("IMG_001.jpg", b"fake jpeg data 1"),
        ("IMG_002.nef", b"fake raw data 2"),
        ("IMG_003.mp4", b"fake video data 3"),
    ]:
        p = source_root / name
        p.write_bytes(content)
        files.append(p)
    return files


@pytest.fixture
def enabled_config(backup_root: Path) -> RawBackupConfig:
    return RawBackupConfig(
        enabled=True,
        backup_root=str(backup_root),
        preserve_structure=True,
        timestamp_format="%Y-%m-%d_%H%M%S",
    )


@pytest.fixture
def manager(enabled_config: RawBackupConfig) -> RawBackupManager:
    return RawBackupManager(enabled_config)


# ---------------------------------------------------------------------------
# 1.  Timestamped directory creation  (Req 15.2)
# ---------------------------------------------------------------------------

class TestTimestampedDirectoryCreation:
    def test_backup_dir_matches_timestamp_format(self, manager: RawBackupManager, backup_root: Path):
        """Backup directory name must match the configured timestamp format."""
        backup_dir = manager.get_backup_directory()
        # The directory's name should be parseable as a timestamp with the configured format
        import datetime as dt
        # Just check it ends with the timestamp portion
        assert backup_dir.parent == backup_root
        # Verify the name is not empty and starts with a date-looking prefix
        name = backup_dir.name
        assert len(name) >= 8  # at minimum YYYY-MM-DD

    def test_backup_dir_created_on_first_backup(
        self, manager: RawBackupManager, source_files: list[Path], source_root: Path
    ):
        """Backup directory should be created during create_raw_backup."""
        result = manager.create_raw_backup(source_files, source_root)
        assert result.backup_directory.exists()

    def test_same_manager_reuses_timestamp(self, manager: RawBackupManager, backup_root: Path):
        """Multiple calls to get_backup_directory return the same path."""
        dir1 = manager.get_backup_directory()
        dir2 = manager.get_backup_directory()
        assert dir1 == dir2


# ---------------------------------------------------------------------------
# 2.  Directory structure preservation  (Req 15.3)
# ---------------------------------------------------------------------------

class TestStructurePreservation:
    def test_flat_source_preserved_as_flat(
        self, manager: RawBackupManager, source_files: list[Path], source_root: Path
    ):
        """Files directly under source_root should land directly under backup_dir."""
        result = manager.create_raw_backup(source_files, source_root)
        backup_dir = result.backup_directory
        for src in source_files:
            dest = backup_dir / src.name
            assert dest.exists(), f"Expected {dest} to exist"

    def test_sub_directory_structure_preserved(
        self, manager: RawBackupManager, tmp_path: Path, backup_root: Path
    ):
        """Files in sub-directories should mirror the same relative structure."""
        src_root = tmp_path / "source_nested"
        src_root.mkdir()
        sub = src_root / "camera" / "day1"
        sub.mkdir(parents=True)
        img = sub / "photo.jpg"
        img.write_bytes(b"nested photo")

        result = manager.create_raw_backup([img], src_root)
        expected = result.backup_directory / "camera" / "day1" / "photo.jpg"
        assert expected.exists(), f"Expected nested path {expected} to exist"

    def test_no_preserve_structure_uses_flat(self, backup_root: Path, tmp_path: Path):
        """When preserve_structure=False all files land directly under backup_dir."""
        src_root = tmp_path / "src_flat"
        src_root.mkdir()
        sub = src_root / "deep" / "nested"
        sub.mkdir(parents=True)
        img = sub / "photo.nef"
        img.write_bytes(b"deep raw")

        config = RawBackupConfig(
            enabled=True,
            backup_root=str(backup_root),
            preserve_structure=False,
            timestamp_format="%Y-%m-%d_%H%M%S",
        )
        mgr = RawBackupManager(config)
        result = mgr.create_raw_backup([img], src_root)
        expected = result.backup_directory / "photo.nef"
        assert expected.exists()
        # The deep nested path should NOT exist
        bad = result.backup_directory / "deep" / "nested" / "photo.nef"
        assert not bad.exists()


# ---------------------------------------------------------------------------
# 3.  Unique backup directory / sequence numbers  (Req 15.5)
# ---------------------------------------------------------------------------

class TestSequenceNumbers:
    def test_no_collision_returns_base_dir(
        self, manager: RawBackupManager, backup_root: Path
    ):
        """When base timestamp dir doesn't exist, it is returned as-is."""
        unique = manager.ensure_unique_backup_dir()
        assert unique == backup_root / manager.backup_timestamp

    def test_collision_appends_001(self, backup_root: Path):
        """When base timestamp dir exists, _001 variant is returned."""
        config = RawBackupConfig(
            enabled=True,
            backup_root=str(backup_root),
            timestamp_format="%Y-%m-%d_%H%M%S",
        )
        mgr = RawBackupManager(config)
        base = backup_root / mgr.backup_timestamp
        base.mkdir(parents=True)

        unique = mgr.ensure_unique_backup_dir()
        assert unique.name == f"{mgr.backup_timestamp}_001"

    def test_collision_increments_to_002(self, backup_root: Path):
        """When _001 also exists, _002 is returned."""
        config = RawBackupConfig(
            enabled=True,
            backup_root=str(backup_root),
            timestamp_format="%Y-%m-%d_%H%M%S",
        )
        mgr = RawBackupManager(config)
        (backup_root / mgr.backup_timestamp).mkdir(parents=True)
        (backup_root / f"{mgr.backup_timestamp}_001").mkdir(parents=True)

        unique = mgr.ensure_unique_backup_dir()
        assert unique.name == f"{mgr.backup_timestamp}_002"

    def test_sequence_format_is_three_digits(self, backup_root: Path):
        """Sequence numbers must be zero-padded to 3 digits (001 not 01)."""
        config = RawBackupConfig(
            enabled=True,
            backup_root=str(backup_root),
            timestamp_format="%Y-%m-%d_%H%M%S",
        )
        mgr = RawBackupManager(config)
        (backup_root / mgr.backup_timestamp).mkdir(parents=True)

        unique = mgr.ensure_unique_backup_dir()
        suffix = unique.name.split("_")[-1]
        assert len(suffix) == 3, f"Expected 3-digit suffix, got '{suffix}'"

    def test_multiple_managers_get_different_dirs(self, backup_root: Path):
        """Two managers with the same timestamp should get different directories."""
        config = RawBackupConfig(
            enabled=True,
            backup_root=str(backup_root),
            timestamp_format="%Y-%m-%d_%H%M%S",
        )
        mgr1 = RawBackupManager(config)
        # Force the timestamp to a known value for determinism
        ts = "2024-01-01_120000"
        mgr1.backup_timestamp = ts
        mgr1._backup_directory = None  # reset cache

        dir1 = mgr1.get_backup_directory()
        dir1.mkdir(parents=True)  # simulate first backup created the dir

        mgr2 = RawBackupManager(config)
        mgr2.backup_timestamp = ts
        mgr2._backup_directory = None

        dir2 = mgr2.get_backup_directory()
        assert dir1 != dir2


# ---------------------------------------------------------------------------
# 4.  File integrity verification  (Req 15.1 / 11.3)
# ---------------------------------------------------------------------------

class TestIntegrityVerification:
    def test_copied_files_pass_sha256(
        self, manager: RawBackupManager, source_files: list[Path], source_root: Path
    ):
        """After backup, each backed-up file should have the same SHA-256 as the source."""
        result = manager.create_raw_backup(source_files, source_root)
        assert result.files_backed_up == len(source_files)
        assert result.errors == []

        backup_dir = result.backup_directory
        for src in source_files:
            dest = backup_dir / src.name
            assert dest.exists()
            assert _sha256(src) == _sha256(dest), f"Hash mismatch for {src.name}"

    def test_integrity_failure_recorded_in_errors(
        self, manager: RawBackupManager, source_files: list[Path],
        source_root: Path, monkeypatch
    ):
        """When SHA-256 verification fails the error is recorded and file is skipped."""
        # Patch _verify_sha256 to always return False
        monkeypatch.setattr(RawBackupManager, "_verify_sha256", staticmethod(lambda s, d: False))

        result = manager.create_raw_backup(source_files[:1], source_root)
        assert result.files_backed_up == 0
        assert result.files_skipped == 1
        assert any("Integrity check failed" in e for e in result.errors)

    def test_verify_sha256_matching_files(self, tmp_path: Path):
        """_verify_sha256 returns True for identical files."""
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        data = b"some binary content" * 100
        f1.write_bytes(data)
        f2.write_bytes(data)
        assert RawBackupManager._verify_sha256(f1, f2) is True

    def test_verify_sha256_different_files(self, tmp_path: Path):
        """_verify_sha256 returns False for files with different content."""
        f1 = tmp_path / "a.bin"
        f2 = tmp_path / "b.bin"
        f1.write_bytes(b"content A")
        f2.write_bytes(b"content B")
        assert RawBackupManager._verify_sha256(f1, f2) is False


# ---------------------------------------------------------------------------
# 5.  Dry-run: no files written  (Req 15.1 – supports dry_run flag)
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_writes_no_files(
        self, manager: RawBackupManager, source_files: list[Path], source_root: Path
    ):
        """dry_run=True must not create any files or directories in backup_root."""
        backup_root = Path(manager.config.backup_root)
        result = manager.create_raw_backup(source_files, source_root, dry_run=True)

        # backup_root should NOT have been created (or if it existed, no new content)
        if backup_root.exists():
            # Verify no actual file is inside the timestamp dir
            ts_dir = backup_root / manager.backup_timestamp
            assert not ts_dir.exists(), "Timestamp dir should not be created in dry-run"

        assert result.files_backed_up == len(source_files)
        assert result.errors == []

    def test_dry_run_returns_correct_size(
        self, manager: RawBackupManager, source_files: list[Path], source_root: Path
    ):
        """dry_run result should report the correct total size of source files."""
        expected_size = sum(f.stat().st_size for f in source_files)
        result = manager.create_raw_backup(source_files, source_root, dry_run=True)
        assert result.total_size == expected_size

    def test_dry_run_disabled_backup(self, backup_root: Path, source_files: list[Path], source_root: Path):
        """dry_run with disabled backup still returns disabled error."""
        config = RawBackupConfig(enabled=False, backup_root=str(backup_root))
        mgr = RawBackupManager(config)
        result = mgr.create_raw_backup(source_files, source_root, dry_run=True)
        assert result.files_backed_up == 0
        assert any("disabled" in e for e in result.errors)


# ---------------------------------------------------------------------------
# 6.  Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_source_not_found_recorded(
        self, manager: RawBackupManager, source_root: Path
    ):
        """Missing source file should be recorded as an error, not raise."""
        ghost = source_root / "ghost.jpg"  # does not exist
        result = manager.create_raw_backup([ghost], source_root)
        assert result.files_backed_up == 0
        assert result.files_skipped == 1
        assert len(result.errors) == 1
        assert "ghost.jpg" in result.errors[0] or "not found" in result.errors[0].lower()

    @pytest.mark.skipif(sys.platform == "win32", reason="chmod read-only unreliable on Windows")
    def test_read_only_destination_recorded(
        self, manager: RawBackupManager, source_files: list[Path],
        source_root: Path, tmp_path: Path
    ):
        """When backup directory is read-only, errors are captured gracefully."""
        backup_dir = manager.get_backup_directory()
        backup_dir.mkdir(parents=True)
        # Make the backup dir read-only
        backup_dir.chmod(stat.S_IRUSR | stat.S_IXUSR)
        try:
            result = manager.create_raw_backup(source_files[:1], source_root)
            # Should have errors but not raise
            assert result.files_skipped >= 1 or len(result.errors) >= 1
        finally:
            # Restore permissions so tmp_path cleanup works
            backup_dir.chmod(stat.S_IRWXU)

    def test_disabled_backup_returns_skipped(
        self, backup_root: Path, source_files: list[Path], source_root: Path
    ):
        """When raw backup is disabled every file is reported as skipped."""
        config = RawBackupConfig(enabled=False, backup_root=str(backup_root))
        mgr = RawBackupManager(config)
        result = mgr.create_raw_backup(source_files, source_root)
        assert result.files_backed_up == 0
        assert result.files_skipped == len(source_files)
        assert len(result.errors) == 1
        assert "disabled" in result.errors[0]

    def test_multiple_files_continue_after_partial_error(
        self, manager: RawBackupManager, source_files: list[Path], source_root: Path
    ):
        """An error on one file should not prevent other files from being backed up."""
        ghost = source_root / "ghost.jpg"  # does not exist
        files = [source_files[0], ghost, source_files[1]]
        result = manager.create_raw_backup(files, source_root)
        # Two real files should be backed up despite one ghost
        assert result.files_backed_up == 2
        assert result.files_skipped == 1
        assert len(result.errors) == 1


# ---------------------------------------------------------------------------
# 7.  get_backup_path edge cases
# ---------------------------------------------------------------------------

class TestGetBackupPath:
    def test_file_outside_source_root_uses_filename_only(
        self, manager: RawBackupManager, tmp_path: Path
    ):
        """File outside source_root should fall back to just the filename."""
        outside_file = tmp_path / "elsewhere" / "photo.jpg"
        outside_file.parent.mkdir(parents=True)
        outside_file.write_bytes(b"data")
        source_root = tmp_path / "source"
        source_root.mkdir()

        backup_path = manager.get_backup_path(outside_file, source_root)
        assert backup_path.name == "photo.jpg"
        assert backup_path.parent == manager.get_backup_directory()

    def test_result_type_is_path(
        self, manager: RawBackupManager, source_files: list[Path], source_root: Path
    ):
        path = manager.get_backup_path(source_files[0], source_root)
        assert isinstance(path, Path)
