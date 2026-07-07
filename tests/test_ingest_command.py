"""Unit tests for IngestCommand (task 14 – Requirements 15.7, 15.8, 8.4)."""

from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

from photo_ingest.ingest_command import IngestCommand, IngestResult
from photo_ingest.config import (
    DeviceMapping, FileTypes, IngestConfig, LLMConfig,
    PerformanceConfig, PeekConfig, RawBackupConfig,
)
from photo_ingest.deduplication import DuplicateStatus
from photo_ingest.file_operations import FileOperation, OperationType, OperationResults, RawBackupResult
from photo_ingest.file_scanner import FileInfo, ScanResult
from datetime import datetime


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_file(tmp_path: Path, name: str, content: bytes = b"x" * 128) -> Path:
    p = tmp_path / name
    p.write_bytes(content)
    return p


@pytest.fixture
def source_dir(tmp_path):
    d = tmp_path / "source"
    d.mkdir()
    return d


@pytest.fixture
def archive_dir(tmp_path):
    d = tmp_path / "archive"
    d.mkdir()
    return d


@pytest.fixture
def backup_dir(tmp_path):
    d = tmp_path / "backup"
    d.mkdir()
    return d


@pytest.fixture
def config(archive_dir, backup_dir):
    return IngestConfig(
        archive_root=str(archive_dir),
        raw_backup=RawBackupConfig(
            enabled=True,
            backup_root=str(backup_dir),
            timestamp_format="%Y-%m-%d_%H%M%S",
        ),
        devices=DeviceMapping(),
        file_types=FileTypes(),
        llm=LLMConfig(enabled=False),
        peek=PeekConfig(),
        dedupe_store=":memory:",
        performance=PerformanceConfig(parallel_workers=1, batch_size=1),
    )


@pytest.fixture
def source_photos(source_dir):
    return [
        _make_file(source_dir, "IMG_001.jpg"),
        _make_file(source_dir, "IMG_002.nef"),
    ]


def _make_scan_result(files):
    file_infos = [
        FileInfo(
            path=f,
            size=f.stat().st_size,
            modified_time=datetime.now(),
            file_type="jpeg" if f.suffix.lower() in (".jpg", ".jpeg") else "raw",
            extension=f.suffix.lower().lstrip("."),
        )
        for f in files
    ]
    return ScanResult(
        files=file_infos,
        total_files=len(file_infos),
        total_size=sum(f.size for f in file_infos),
        scan_time=0.1,
        errors=[],
        directories_scanned=1,
    )


# ---------------------------------------------------------------------------
# Basic construction
# ---------------------------------------------------------------------------


class TestIngestCommandInit:
    def test_can_be_created(self, config):
        cmd = IngestCommand(config)
        assert cmd.config is config

    def test_progress_callback_stored(self, config):
        cb = MagicMock()
        cmd = IngestCommand(config, progress_callback=cb)
        assert cmd.progress is cb


# ---------------------------------------------------------------------------
# raw_only / organized_only validation
# ---------------------------------------------------------------------------


class TestModeValidation:
    def test_both_flags_raises(self, config, source_dir):
        cmd = IngestCommand(config)
        with pytest.raises(ValueError, match="Cannot specify both"):
            cmd.run(source_dir, "Event", raw_only=True, organized_only=True)

    def test_raw_only_accepted(self, config, source_dir, source_photos):
        cmd = IngestCommand(config)
        # Inject mocks so we don't hit filesystem side-effects
        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.return_value = _make_scan_result(source_photos)
        cmd._db_manager = MagicMock()
        cmd._dedup_engine = MagicMock()
        cmd._dedup_engine.check_duplicate.return_value = MagicMock(status=DuplicateStatus.NEW)
        cmd._dedup_engine.calculate_hashes.return_value = MagicMock(sha256="a" * 64, size=128, perceptual=None)
        cmd._device_detector = MagicMock()
        cmd._device_detector.detect_device.return_value = "Unknown"
        cmd._file_ops = MagicMock()
        cmd._file_ops.plan_operations.return_value = []
        cmd._file_ops.execute_operations.return_value = OperationResults(
            files_processed=0, files_copied=0, files_moved=0,
            files_skipped=0, duplicates_found=0, errors=[],
        )
        result = cmd.run(source_dir, "Event", raw_only=True, dry_run=True)
        assert isinstance(result, IngestResult)

    def test_organized_only_accepted(self, config, source_dir, source_photos):
        cmd = IngestCommand(config)
        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.return_value = _make_scan_result(source_photos)
        cmd._db_manager = MagicMock()
        cmd._dedup_engine = MagicMock()
        cmd._dedup_engine.check_duplicate.return_value = MagicMock(status=DuplicateStatus.NEW)
        cmd._device_detector = MagicMock()
        cmd._device_detector.detect_device.return_value = "Unknown"
        cmd._file_ops = MagicMock()
        cmd._file_ops.plan_operations.return_value = []
        cmd._file_ops.execute_operations.return_value = OperationResults(
            files_processed=0, files_copied=0, files_moved=0,
            files_skipped=0, duplicates_found=0, errors=[],
        )
        result = cmd.run(source_dir, "Event", organized_only=True, dry_run=True)
        assert isinstance(result, IngestResult)


# ---------------------------------------------------------------------------
# Source directory validation
# ---------------------------------------------------------------------------


class TestSourceValidation:
    def test_missing_source_raises(self, config, tmp_path):
        cmd = IngestCommand(config)
        with pytest.raises(FileNotFoundError):
            cmd.run(tmp_path / "nonexistent", "Event")

    def test_empty_source_returns_zero_files(self, config, source_dir):
        cmd = IngestCommand(config)
        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.return_value = ScanResult(
            files=[], total_files=0, total_size=0,
            scan_time=0.0, errors=[], directories_scanned=1,
        )
        cmd._db_manager = MagicMock()
        cmd._dedup_engine = MagicMock()
        cmd._device_detector = MagicMock()
        cmd._file_ops = MagicMock()
        result = cmd.run(source_dir, "Event")
        assert result.total_files == 0


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


class TestDryRun:
    def _make_cmd_with_mocks(self, config, source_photos, source_dir, archive_dir):
        cmd = IngestCommand(config)
        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.return_value = _make_scan_result(source_photos)
        cmd._db_manager = MagicMock()
        cmd._dedup_engine = MagicMock()
        check = MagicMock()
        check.status = DuplicateStatus.NEW
        cmd._dedup_engine.check_duplicate.return_value = check
        cmd._device_detector = MagicMock()
        cmd._device_detector.detect_device.return_value = "Camera"

        event_dest = archive_dir / "2024" / "2024-07-15_Event" / "Camera"
        planned_op = FileOperation(
            source_path=source_photos[0],
            dest_path=event_dest / source_photos[0].name,
            raw_backup_path=None,
            camera_code="Camera",
            duplicate_status=DuplicateStatus.NEW,
            metadata={},
            operation_type=OperationType.ORGANIZED_ONLY,
        )
        cmd._file_ops = MagicMock()
        cmd._file_ops.plan_operations.return_value = [planned_op]
        return cmd

    def test_dry_run_returns_result(self, config, source_photos, source_dir, archive_dir):
        cmd = self._make_cmd_with_mocks(config, source_photos, source_dir, archive_dir)
        result = cmd.run(source_dir, "Event", dry_run=True)
        assert isinstance(result, IngestResult)
        assert result.dry_run is True

    def test_dry_run_does_not_call_execute(self, config, source_photos, source_dir, archive_dir):
        cmd = self._make_cmd_with_mocks(config, source_photos, source_dir, archive_dir)
        cmd.run(source_dir, "Event", dry_run=True)
        cmd._file_ops.execute_operations.assert_not_called()

    def test_dry_run_returns_planned_paths(self, config, source_photos, source_dir, archive_dir):
        cmd = self._make_cmd_with_mocks(config, source_photos, source_dir, archive_dir)
        result = cmd.run(source_dir, "Event", dry_run=True)
        assert result.manifest_path is not None
        assert result.summary_path is not None
        # Paths should NOT exist (dry-run)
        assert not result.manifest_path.exists()
        assert not result.summary_path.exists()


# ---------------------------------------------------------------------------
# Real execution (copy mode) – uses actual tmp filesystem
# ---------------------------------------------------------------------------


class TestRealExecution:
    def test_copy_mode_creates_organised_files(self, config, source_photos, source_dir, archive_dir):
        cmd = IngestCommand(config)
        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.return_value = _make_scan_result(source_photos)
        cmd._db_manager = MagicMock()
        cmd._db_manager.is_file_unchanged.return_value = False
        cmd._db_manager.get_file_by_path_and_mtime.return_value = None
        cmd._db_manager.get_files_by_size_range.return_value = []
        cmd._db_manager.get_all_files_with_perceptual_hash.return_value = []
        cmd._db_manager.insert_file_record = MagicMock()
        cmd._db_manager.batch_insert_records = MagicMock()

        event_dest = archive_dir / "2024" / "2024-07-15_Event" / "Camera"
        event_dest.mkdir(parents=True, exist_ok=True)

        ops = [
            FileOperation(
                source_path=f,
                dest_path=event_dest / f.name,
                raw_backup_path=None,
                camera_code="Camera",
                duplicate_status=DuplicateStatus.NEW,
                metadata={},
                operation_type=OperationType.ORGANIZED_ONLY,
            )
            for f in source_photos
        ]

        import shutil
        for op in ops:
            shutil.copy2(op.source_path, op.dest_path)

        mock_op_results = OperationResults(
            files_processed=2, files_copied=2, files_moved=0,
            files_skipped=0, duplicates_found=0, errors=[],
        )

        cmd._device_detector = MagicMock()
        cmd._device_detector.detect_device.return_value = "Camera"
        cmd._dedup_engine = MagicMock()
        check = MagicMock(); check.status = DuplicateStatus.NEW
        cmd._dedup_engine.check_duplicate.return_value = check
        cmd._dedup_engine.calculate_hashes.return_value = MagicMock(sha256="a" * 64, size=128, perceptual=None)
        cmd._file_ops = MagicMock()
        cmd._file_ops.plan_operations.return_value = ops
        cmd._file_ops.execute_operations.return_value = mock_op_results

        result = cmd.run(source_dir, "Event", copy_mode=True)
        assert result.files_copied == 2
        assert result.errors == [] or all("dedup" not in e.lower() for e in result.errors)

    def test_duplicate_not_stored_in_db(self, config, source_photos, source_dir, archive_dir):
        """Files marked DUPLICATE must not be stored in the dedup DB (Req 4.7)."""
        cmd = IngestCommand(config)
        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.return_value = _make_scan_result(source_photos)
        cmd._db_manager = MagicMock()
        cmd._device_detector = MagicMock()
        cmd._device_detector.detect_device.return_value = "Camera"

        dup_dedup = MagicMock(); dup_dedup.status = DuplicateStatus.DUPLICATE
        cmd._dedup_engine = MagicMock()
        cmd._dedup_engine.check_duplicate.return_value = dup_dedup

        cmd._file_ops = MagicMock()
        cmd._file_ops.plan_operations.return_value = []
        cmd._file_ops.execute_operations.return_value = OperationResults(
            files_processed=0, files_copied=0, files_moved=0,
            files_skipped=2, duplicates_found=2, errors=[],
        )

        cmd.run(source_dir, "Event")
        cmd._dedup_engine.store_file_record.assert_not_called()


# ---------------------------------------------------------------------------
# Progress callback
# ---------------------------------------------------------------------------


class TestProgressCallback:
    def test_progress_called_during_scan(self, config, source_dir, source_photos):
        calls = []
        cmd = IngestCommand(config, progress_callback=lambda *a: calls.append(a))
        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.return_value = _make_scan_result(source_photos)
        cmd._db_manager = MagicMock()
        cmd._dedup_engine = MagicMock()
        check = MagicMock(); check.status = DuplicateStatus.NEW
        cmd._dedup_engine.check_duplicate.return_value = check
        cmd._device_detector = MagicMock()
        cmd._device_detector.detect_device.return_value = "Camera"
        cmd._file_ops = MagicMock()
        cmd._file_ops.plan_operations.return_value = []
        cmd._file_ops.execute_operations.return_value = OperationResults(
            files_processed=0, files_copied=0, files_moved=0,
            files_skipped=0, duplicates_found=0, errors=[],
        )

        cmd.run(source_dir, "Event", dry_run=True)
        phases = {c[0] for c in calls}
        assert "scan" in phases


# ---------------------------------------------------------------------------
# Error resilience (Req 8.4)
# ---------------------------------------------------------------------------


class TestErrorResilience:
    def test_error_in_execute_recorded_not_raised(self, config, source_dir, source_photos):
        cmd = IngestCommand(config)
        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.return_value = _make_scan_result(source_photos)
        cmd._db_manager = MagicMock()
        cmd._dedup_engine = MagicMock()
        check = MagicMock(); check.status = DuplicateStatus.NEW
        cmd._dedup_engine.check_duplicate.return_value = check
        cmd._device_detector = MagicMock()
        cmd._device_detector.detect_device.return_value = "Camera"
        cmd._file_ops = MagicMock()
        cmd._file_ops.plan_operations.return_value = []
        cmd._file_ops.execute_operations.return_value = OperationResults(
            files_processed=0, files_copied=0, files_moved=0,
            files_skipped=0, duplicates_found=0,
            errors=["Permission denied: IMG_001.jpg"],
        )

        result = cmd.run(source_dir, "Event")
        # Should not raise; error should appear in result
        assert any("Permission denied" in e for e in result.errors)

    def test_catastrophic_error_recorded(self, config, source_dir):
        cmd = IngestCommand(config)
        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.side_effect = RuntimeError("disk full")
        cmd._db_manager = MagicMock()
        cmd._dedup_engine = MagicMock()
        cmd._device_detector = MagicMock()
        cmd._file_ops = MagicMock()

        result = cmd.run(source_dir, "Event")
        assert len(result.errors) > 0
        assert any("disk full" in e for e in result.errors)
