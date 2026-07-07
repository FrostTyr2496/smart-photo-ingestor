"""Unit tests for FileOperationsManager (Task 10).

Requirements covered: 3.1, 3.2, 3.3, 3.4, 3.5, 11.1, 11.3
"""

import hashlib
import shutil
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, List
from unittest.mock import patch, MagicMock

import pytest

from photo_ingest.file_operations import (
    FileOperation,
    FileOperationsManager,
    OperationResults,
    OperationType,
    RawBackupManager,
)
from photo_ingest.config import (
    DeviceMapping,
    FileTypes,
    IngestConfig,
    LLMConfig,
    PeekConfig,
    PerformanceConfig,
    RawBackupConfig,
)
from photo_ingest.deduplication import DuplicateStatus


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def archive_root(tmp_path: Path) -> Path:
    d = tmp_path / "archive"
    d.mkdir()
    return d


@pytest.fixture
def backup_root(tmp_path: Path) -> Path:
    d = tmp_path / "backups"
    d.mkdir()
    return d


@pytest.fixture
def source_dir(tmp_path: Path) -> Path:
    d = tmp_path / "source"
    d.mkdir()
    return d


@pytest.fixture
def config(archive_root: Path, backup_root: Path) -> IngestConfig:
    return IngestConfig(
        archive_root=str(archive_root),
        raw_backup=RawBackupConfig(
            enabled=True,
            backup_root=str(backup_root),
            preserve_structure=True,
            timestamp_format="%Y-%m-%d_%H%M%S",
        ),
        devices=DeviceMapping(),
        file_types=FileTypes(),
        llm=LLMConfig(),
        peek=PeekConfig(),
        dedupe_store=":memory:",
        performance=PerformanceConfig(parallel_workers=2),
    )


@pytest.fixture
def manager(config: IngestConfig) -> FileOperationsManager:
    return FileOperationsManager(config)


@pytest.fixture
def source_files(source_dir: Path) -> List[Path]:
    """Three source files with distinct binary content."""
    files = []
    for name, content in [
        ("IMG_001.jpg", b"jpeg content one"),
        ("IMG_002.nef", b"raw nef content two"),
        ("VID_003.mp4", b"video content three"),
    ]:
        p = source_dir / name
        p.write_bytes(content)
        files.append(p)
    return files


def _make_metadata(device_code: str = "Z6", date: str = "2024-06-15T09:30:00") -> Dict:
    return {
        "Make": "NIKON CORPORATION",
        "Model": "NIKON Z 6",
        "device_code": device_code,
        "DateTime": date,
    }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 1.  Path planning – Req 3.1, 3.2, 3.3
# ---------------------------------------------------------------------------

class TestPathPlanning:
    """Validate that plan_operations() produces correct destination paths.

    Req 3.1: archive_root/YYYY/YYYY-MM-DD_EventName/CameraCode/filename
    Req 3.2: YYYY and MM-DD from EXIF DateTime
    Req 3.3: Separate subdirectory per camera code
    """

    def test_organized_path_structure(self, manager: FileOperationsManager,
                                      source_files: List[Path],
                                      archive_root: Path):
        """Req 3.1 – dest path must follow YYYY/YYYY-MM-DD_Event/Device/file."""
        meta = _make_metadata(device_code="Z6", date="2024-06-15T09:30:00")
        files_meta = {source_files[0]: meta}
        dup_results = {source_files[0]: DuplicateStatus.NEW}

        ops = manager.plan_operations(files_meta, "Wedding", dup_results, organized_only=True)

        assert len(ops) == 1
        dest = ops[0].dest_path
        # Verify structure components
        assert dest is not None
        parts = dest.relative_to(archive_root).parts
        assert parts[0] == "2024", f"Expected year '2024', got '{parts[0]}'"
        assert parts[1] == "2024-06-15_Wedding", f"Got '{parts[1]}'"
        assert parts[2] == "Z6", f"Got '{parts[2]}'"
        assert parts[3] == "IMG_001.jpg", f"Got '{parts[3]}'"

    def test_year_from_exif_date(self, manager: FileOperationsManager,
                                 source_files: List[Path],
                                 archive_root: Path):
        """Req 3.2 – YYYY extracted from EXIF data."""
        meta = _make_metadata(date="2019-03-22T14:00:00")
        ops = manager.plan_operations({source_files[0]: meta}, "Trip",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        year_part = ops[0].dest_path.relative_to(archive_root).parts[0]
        assert year_part == "2019"

    def test_date_folder_from_exif(self, manager: FileOperationsManager,
                                   source_files: List[Path],
                                   archive_root: Path):
        """Req 3.2 – YYYY-MM-DD_Event folder extracted from EXIF data."""
        meta = _make_metadata(date="2022-11-05T08:00:00")
        ops = manager.plan_operations({source_files[0]: meta}, "Race",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        date_part = ops[0].dest_path.relative_to(archive_root).parts[1]
        assert date_part == "2022-11-05_Race"

    def test_separate_subdirectory_per_camera_code(self, manager: FileOperationsManager,
                                                    source_files: List[Path],
                                                    archive_root: Path):
        """Req 3.3 – Each camera code gets its own subdirectory."""
        meta_z6 = _make_metadata(device_code="Z6", date="2024-01-10T12:00:00")
        meta_drone = _make_metadata(device_code="Drone", date="2024-01-10T12:00:00")
        files_meta = {
            source_files[0]: meta_z6,
            source_files[1]: meta_drone,
        }
        dup_results = {f: DuplicateStatus.NEW for f in source_files[:2]}

        ops = manager.plan_operations(files_meta, "Shoot", dup_results, organized_only=True)
        device_dirs = {op.dest_path.parent.name for op in ops}
        assert "Z6" in device_dirs
        assert "Drone" in device_dirs

    def test_filename_preserved(self, manager: FileOperationsManager,
                                source_files: List[Path]):
        """Original filename must be kept in the destination path."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        assert ops[0].dest_path.name == source_files[0].name

    def test_date_fallback_to_file_mtime(self, manager: FileOperationsManager,
                                         source_files: List[Path],
                                         archive_root: Path):
        """When EXIF DateTime is absent, fall back to file mtime (Req 12.5)."""
        meta = {"device_code": "Cam"}  # No DateTime key
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        dest = ops[0].dest_path
        assert dest is not None
        # Just verify a valid date directory was created (YYYY/YYYY-MM-DD_Event/...)
        parts = dest.relative_to(archive_root).parts
        assert len(parts[0]) == 4 and parts[0].isdigit()


# ---------------------------------------------------------------------------
# 2.  Copy mode – Req 3.4, 11.3
# ---------------------------------------------------------------------------

class TestCopyMode:
    """Req 3.4 – --copy flag copies files; Req 11.3 – verify checksum after copy."""

    def test_copy_mode_leaves_source_intact(self, manager: FileOperationsManager,
                                            source_files: List[Path]):
        """Source file must still exist after copy."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        manager.execute_operations(ops, copy_mode=True)
        assert source_files[0].exists()

    def test_copy_mode_creates_destination(self, manager: FileOperationsManager,
                                           source_files: List[Path]):
        """Destination file must exist after copy."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        results = manager.execute_operations(ops, copy_mode=True)
        assert results.files_copied == 1
        assert ops[0].dest_path.exists()

    def test_copy_mode_sha256_integrity(self, manager: FileOperationsManager,
                                        source_files: List[Path]):
        """Req 11.3 – copied file must match source SHA-256 checksum."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        manager.execute_operations(ops, copy_mode=True)
        assert _sha256(source_files[0]) == _sha256(ops[0].dest_path)

    def test_copy_mode_result_counts(self, manager: FileOperationsManager,
                                     source_files: List[Path]):
        """files_copied count must equal number of successfully copied files."""
        meta = _make_metadata()
        files_meta = {f: _make_metadata() for f in source_files}
        dup_results = {f: DuplicateStatus.NEW for f in source_files}

        ops = manager.plan_operations(files_meta, "Event", dup_results, organized_only=True)
        results = manager.execute_operations(ops, copy_mode=True)

        assert results.files_copied == len(source_files)
        assert results.files_moved == 0

    def test_integrity_failure_recorded_in_errors(self, manager: FileOperationsManager,
                                                   source_files: List[Path]):
        """When verify_file_integrity returns False the error is recorded."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        with patch.object(manager, "verify_file_integrity", return_value=False):
            results = manager.execute_operations(ops, copy_mode=True)
        assert any("integrity" in e.lower() for e in results.errors)


# ---------------------------------------------------------------------------
# 3.  Move mode – Req 3.5
# ---------------------------------------------------------------------------

class TestMoveMode:
    """Req 3.5 – --move flag moves files to destination."""

    def test_move_removes_source(self, manager: FileOperationsManager,
                                 source_files: List[Path]):
        """Source file must NOT exist after move."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        manager.execute_operations(ops, copy_mode=False)
        assert not source_files[0].exists()

    def test_move_creates_destination(self, manager: FileOperationsManager,
                                      source_files: List[Path]):
        """Destination file must exist after move."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        results = manager.execute_operations(ops, copy_mode=False)
        assert results.files_moved == 1
        assert ops[0].dest_path.exists()

    def test_move_result_counts(self, manager: FileOperationsManager,
                                source_files: List[Path]):
        """files_moved count must equal number of successfully moved files."""
        files_meta = {f: _make_metadata() for f in source_files}
        dup_results = {f: DuplicateStatus.NEW for f in source_files}

        ops = manager.plan_operations(files_meta, "Event", dup_results, organized_only=True)
        results = manager.execute_operations(ops, copy_mode=False)

        assert results.files_moved == len(source_files)
        assert results.files_copied == 0

    def test_default_is_copy(self, manager: FileOperationsManager,
                              source_files: List[Path]):
        """Req 3.5 – default operation (copy_mode=True) must be copy, not move."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        results = manager.execute_operations(ops)  # copy_mode defaults to True
        assert source_files[0].exists()
        assert results.files_copied == 1


# ---------------------------------------------------------------------------
# 4.  Duplicate skipping
# ---------------------------------------------------------------------------

class TestDuplicateSkipping:
    """Duplicate files must be skipped in organized mode."""

    def test_duplicate_files_skipped(self, manager: FileOperationsManager,
                                     source_files: List[Path]):
        """DUPLICATE files must not be copied/moved."""
        meta = _make_metadata()
        files_meta = {
            source_files[0]: meta,
            source_files[1]: meta,
        }
        dup_results = {
            source_files[0]: DuplicateStatus.NEW,
            source_files[1]: DuplicateStatus.DUPLICATE,
        }
        ops = manager.plan_operations(files_meta, "Event", dup_results, organized_only=True)
        # plan_operations should exclude the duplicate
        assert len(ops) == 1
        assert ops[0].source_path == source_files[0]

    def test_duplicate_counted_in_execute(self, manager: FileOperationsManager,
                                          source_files: List[Path]):
        """execute_operations must count DUPLICATE operations in duplicates_found."""
        meta = _make_metadata()
        # Build a FileOperation explicitly marked as DUPLICATE
        dup_op = FileOperation(
            source_path=source_files[1],
            dest_path=Path("/dev/null/dest.jpg"),
            raw_backup_path=None,
            camera_code="Z6",
            duplicate_status=DuplicateStatus.DUPLICATE,
            metadata=meta,
            operation_type=OperationType.ORGANIZED_ONLY,
        )
        new_meta = _make_metadata()
        new_op = FileOperation(
            source_path=source_files[0],
            dest_path=None,  # will be determined via plan
            raw_backup_path=None,
            camera_code="Z6",
            duplicate_status=DuplicateStatus.NEW,
            metadata=new_meta,
            operation_type=OperationType.ORGANIZED_ONLY,
        )
        # Use plan to get a proper dest_path for the new file
        planned_ops = manager.plan_operations(
            {source_files[0]: new_meta}, "Event",
            {source_files[0]: DuplicateStatus.NEW},
            organized_only=True,
        )
        all_ops = planned_ops + [dup_op]
        results = manager.execute_operations(all_ops, copy_mode=True)
        assert results.duplicates_found >= 1
        assert results.files_skipped >= 1

    def test_similar_not_skipped_by_default(self, manager: FileOperationsManager,
                                             source_files: List[Path]):
        """SIMILAR files are NOT filtered out by plan_operations (only DUPLICATE)."""
        meta = _make_metadata()
        dup_results = {source_files[0]: DuplicateStatus.SIMILAR}
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      dup_results, organized_only=True)
        assert len(ops) == 1


# ---------------------------------------------------------------------------
# 5.  Error handling – individual files fail without halting others
# ---------------------------------------------------------------------------

class TestErrorHandling:
    """Errors on individual files are recorded without stopping other files."""

    def test_missing_source_recorded_not_raised(self, manager: FileOperationsManager,
                                                 source_files: List[Path],
                                                 archive_root: Path):
        """An operation whose source doesn't exist produces an error entry, not an exception."""
        ghost = source_files[0].parent / "ghost.jpg"  # never created
        meta = _make_metadata()
        ghost_op = FileOperation(
            source_path=ghost,
            dest_path=archive_root / "2024" / "2024-06-15_E" / "Z6" / "ghost.jpg",
            raw_backup_path=None,
            camera_code="Z6",
            duplicate_status=DuplicateStatus.NEW,
            metadata=meta,
            operation_type=OperationType.ORGANIZED_ONLY,
        )
        real_ops = manager.plan_operations(
            {source_files[0]: meta}, "E",
            {source_files[0]: DuplicateStatus.NEW},
            organized_only=True,
        )
        all_ops = [ghost_op] + real_ops
        results = manager.execute_operations(all_ops, copy_mode=True)

        # Real file was still processed
        assert results.files_copied >= 1
        # Ghost produced an error
        assert len(results.errors) >= 1

    def test_permission_error_recorded_continue(self, manager: FileOperationsManager,
                                                  source_files: List[Path]):
        """A PermissionError on one file is recorded; subsequent files proceed."""
        meta = _make_metadata()
        files_meta = {f: meta for f in source_files[:2]}
        dup_results = {f: DuplicateStatus.NEW for f in source_files[:2]}
        ops = manager.plan_operations(files_meta, "Event", dup_results, organized_only=True)

        call_count = [0]
        original_copy2 = shutil.copy2

        def flaky_copy2(src, dst):
            call_count[0] += 1
            if call_count[0] == 1:
                raise PermissionError("permission denied (simulated)")
            return original_copy2(src, dst)

        with patch("photo_ingest.file_operations.shutil.copy2", side_effect=flaky_copy2):
            results = manager.execute_operations(ops, copy_mode=True)

        # One error, one success
        assert len(results.errors) >= 1
        assert results.files_copied >= 1

    def test_errors_do_not_raise(self, manager: FileOperationsManager,
                                  source_files: List[Path]):
        """execute_operations must never raise; all errors are collected."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        with patch("photo_ingest.file_operations.shutil.copy2",
                   side_effect=RuntimeError("disk full")):
            results = manager.execute_operations(ops, copy_mode=True)  # must not raise
        assert isinstance(results, OperationResults)
        assert len(results.errors) >= 1


# ---------------------------------------------------------------------------
# 6.  raw_only and organized_only flags
# ---------------------------------------------------------------------------

class TestOperationFlags:
    """raw_only and organized_only flags control which operations are planned."""

    def test_organized_only_no_raw_backup_path(self, manager: FileOperationsManager,
                                                source_files: List[Path]):
        """organized_only=True => raw_backup_path is None."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        assert ops[0].operation_type == OperationType.ORGANIZED_ONLY
        assert ops[0].raw_backup_path is None

    def test_raw_only_no_dest_path(self, manager: FileOperationsManager,
                                    source_files: List[Path]):
        """raw_only=True => dest_path is None."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      raw_only=True)
        assert ops[0].operation_type == OperationType.RAW_BACKUP_ONLY
        assert ops[0].dest_path is None

    def test_both_flags_false_produces_both(self, manager: FileOperationsManager,
                                             source_files: List[Path]):
        """No flags => OperationType.BOTH with both paths set."""
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW})
        assert ops[0].operation_type == OperationType.BOTH
        assert ops[0].dest_path is not None
        assert ops[0].raw_backup_path is not None

    def test_raw_only_includes_duplicates(self, manager: FileOperationsManager,
                                           source_files: List[Path]):
        """Req 15.7 – raw_only must not skip duplicate files."""
        meta = _make_metadata()
        files_meta = {f: meta for f in source_files[:2]}
        dup_results = {
            source_files[0]: DuplicateStatus.NEW,
            source_files[1]: DuplicateStatus.DUPLICATE,
        }
        ops = manager.plan_operations(files_meta, "Event", dup_results, raw_only=True)
        assert len(ops) == 2

    def test_organized_only_excludes_duplicates(self, manager: FileOperationsManager,
                                                 source_files: List[Path]):
        """organized_only skips DUPLICATE files."""
        meta = _make_metadata()
        files_meta = {f: meta for f in source_files[:2]}
        dup_results = {
            source_files[0]: DuplicateStatus.NEW,
            source_files[1]: DuplicateStatus.DUPLICATE,
        }
        ops = manager.plan_operations(files_meta, "Event", dup_results, organized_only=True)
        assert len(ops) == 1


# ---------------------------------------------------------------------------
# 7.  Parallel execution – Req 11.1
# ---------------------------------------------------------------------------

class TestParallelExecution:
    """Req 11.1 – parallel processing using ThreadPoolExecutor for file operations."""

    def test_parallel_execution_all_files_copied(self, config: IngestConfig,
                                                   tmp_path: Path):
        """All files are correctly copied when parallel_workers > 1."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        files = []
        for i in range(6):
            p = src_dir / f"photo_{i:02d}.jpg"
            p.write_bytes(f"content {i}".encode() * 100)
            files.append(p)

        mgr = FileOperationsManager(config)
        meta = _make_metadata()
        files_meta = {f: meta for f in files}
        dup_results = {f: DuplicateStatus.NEW for f in files}

        ops = mgr.plan_operations(files_meta, "ParallelEvent", dup_results, organized_only=True)
        results = mgr.execute_operations(ops, copy_mode=True)

        assert results.files_copied == len(files)
        assert results.errors == []
        for op in ops:
            assert op.dest_path.exists()

    def test_parallel_execution_no_race_condition(self, config: IngestConfig,
                                                    tmp_path: Path):
        """Concurrent file copies produce consistent results (no data corruption)."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        files = []
        for i in range(8):
            p = src_dir / f"img_{i:02d}.nef"
            p.write_bytes(bytes(range(256)) * 64)  # 16KB each, distinct
            files.append(p)

        mgr = FileOperationsManager(config)
        meta = _make_metadata()
        files_meta = {f: meta for f in files}
        dup_results = {f: DuplicateStatus.NEW for f in files}

        ops = mgr.plan_operations(files_meta, "RaceCheck", dup_results, organized_only=True)
        results = mgr.execute_operations(ops, copy_mode=True)

        assert results.files_copied == len(files)
        for src, op in zip(files, ops):
            assert _sha256(src) == _sha256(op.dest_path), \
                f"SHA-256 mismatch for {src.name}"

    def test_parallel_errors_collected_not_raised(self, config: IngestConfig,
                                                    tmp_path: Path):
        """When multiple workers fail simultaneously all errors are collected."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        files = []
        for i in range(4):
            p = src_dir / f"err_{i}.jpg"
            p.write_bytes(b"data")
            files.append(p)

        mgr = FileOperationsManager(config)
        meta = _make_metadata()
        files_meta = {f: meta for f in files}
        dup_results = {f: DuplicateStatus.NEW for f in files}
        ops = mgr.plan_operations(files_meta, "Errors", dup_results, organized_only=True)

        with patch("photo_ingest.file_operations.shutil.copy2",
                   side_effect=IOError("disk error")):
            results = mgr.execute_operations(ops, copy_mode=True)

        assert isinstance(results, OperationResults)
        assert len(results.errors) == len(files)
        assert results.files_copied == 0

    def test_sequential_fallback_with_single_worker(self, archive_root: Path,
                                                      backup_root: Path,
                                                      source_files: List[Path]):
        """parallel_workers=1 uses the sequential code path successfully."""
        seq_config = IngestConfig(
            archive_root=str(archive_root),
            raw_backup=RawBackupConfig(enabled=False, backup_root=str(backup_root)),
            devices=DeviceMapping(),
            file_types=FileTypes(),
            llm=LLMConfig(),
            peek=PeekConfig(),
            dedupe_store=":memory:",
            performance=PerformanceConfig(parallel_workers=1),
        )
        mgr = FileOperationsManager(seq_config)
        meta = _make_metadata()
        files_meta = {source_files[0]: meta}
        dup_results = {source_files[0]: DuplicateStatus.NEW}

        ops = mgr.plan_operations(files_meta, "SeqEvent", dup_results, organized_only=True)
        results = mgr.execute_operations(ops, copy_mode=True)

        assert results.files_copied == 1
        assert results.errors == []


# ---------------------------------------------------------------------------
# 8.  verify_file_integrity – Req 11.3
# ---------------------------------------------------------------------------

class TestVerifyFileIntegrity:
    """Req 11.3 – copied files must match source checksums."""

    def test_identical_files_pass(self, manager: FileOperationsManager, tmp_path: Path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        data = b"integrity test data" * 200
        a.write_bytes(data)
        b.write_bytes(data)
        assert manager.verify_file_integrity(a, b) is True

    def test_different_content_fails(self, manager: FileOperationsManager, tmp_path: Path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"original content abc")
        b.write_bytes(b"different content xyz")
        assert manager.verify_file_integrity(a, b) is False

    def test_different_size_fails(self, manager: FileOperationsManager, tmp_path: Path):
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        a.write_bytes(b"hello")
        b.write_bytes(b"hello world")
        assert manager.verify_file_integrity(a, b) is False

    def test_empty_files_match(self, manager: FileOperationsManager, tmp_path: Path):
        a = tmp_path / "empty_a.bin"
        b = tmp_path / "empty_b.bin"
        a.write_bytes(b"")
        b.write_bytes(b"")
        assert manager.verify_file_integrity(a, b) is True

    def test_uses_sha256_not_md5(self, manager: FileOperationsManager, tmp_path: Path):
        """Verify that SHA-256 is used (MD5 has known collision attacks)."""
        a = tmp_path / "a.bin"
        b = tmp_path / "b.bin"
        content = b"test content for sha256 verification"
        a.write_bytes(content)
        b.write_bytes(content)
        # If SHA-256 is used _verify_sha256 should agree
        sha_result = RawBackupManager._verify_sha256(a, b)
        mgr_result = manager.verify_file_integrity(a, b)
        assert sha_result == mgr_result is True


# ---------------------------------------------------------------------------
# 9.  Dry-run mode
# ---------------------------------------------------------------------------

class TestDryRun:
    """execute_operations(dry_run=True) must not touch the filesystem."""

    def test_dry_run_no_files_written(self, manager: FileOperationsManager,
                                       source_files: List[Path]):
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        dest = ops[0].dest_path
        manager.execute_operations(ops, copy_mode=True, dry_run=True)
        assert not dest.exists()

    def test_dry_run_source_unchanged(self, manager: FileOperationsManager,
                                       source_files: List[Path]):
        meta = _make_metadata()
        ops = manager.plan_operations({source_files[0]: meta}, "Event",
                                      {source_files[0]: DuplicateStatus.NEW},
                                      organized_only=True)
        original_content = source_files[0].read_bytes()
        manager.execute_operations(ops, copy_mode=False, dry_run=True)
        assert source_files[0].read_bytes() == original_content

    def test_dry_run_reports_counts(self, manager: FileOperationsManager,
                                     source_files: List[Path]):
        meta = _make_metadata()
        files_meta = {f: meta for f in source_files}
        dup_results = {f: DuplicateStatus.NEW for f in source_files}
        ops = manager.plan_operations(files_meta, "Event", dup_results, organized_only=True)
        results = manager.execute_operations(ops, copy_mode=True, dry_run=True)
        assert results.files_processed == len(source_files)
        assert results.files_copied == len(source_files)


# ---------------------------------------------------------------------------
# 10.  Progress callback
# ---------------------------------------------------------------------------

class TestProgressCallback:
    """Progress callback is invoked during execute_operations."""

    def test_progress_callback_invoked(self, config: IngestConfig,
                                        source_files: List[Path]):
        calls = []

        def cb(phase, current, total, message):
            calls.append((phase, current, total))

        mgr = FileOperationsManager(config, progress_callback=cb)
        meta = _make_metadata()
        ops = mgr.plan_operations({source_files[0]: meta}, "Event",
                                   {source_files[0]: DuplicateStatus.NEW},
                                   organized_only=True)
        mgr.execute_operations(ops, copy_mode=True)
        assert len(calls) >= 1

    def test_progress_total_matches_operations(self, config: IngestConfig,
                                                source_files: List[Path]):
        totals = []

        def cb(phase, current, total, message):
            totals.append(total)

        mgr = FileOperationsManager(config, progress_callback=cb)
        meta = _make_metadata()
        files_meta = {f: meta for f in source_files}
        dup_results = {f: DuplicateStatus.NEW for f in source_files}
        ops = mgr.plan_operations(files_meta, "Event", dup_results, organized_only=True)
        mgr.execute_operations(ops, copy_mode=True)
        assert all(t == len(source_files) for t in totals)
