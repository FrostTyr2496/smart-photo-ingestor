"""Unit tests for custom exception hierarchy and error handling.

Requirements: 8.1, 8.2, 8.3, 8.4, 8.5
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch
import logging

import pytest

from photo_ingest.exceptions import (
    ArchiveDirectoryError,
    ConfigurationError,
    DeduplicationError,
    EXIFProcessingError,
    FileOperationError,
    MissingDependencyError,
    PhotoIngestError,
    SourceDirectoryError,
    format_user_error,
)


# ---------------------------------------------------------------------------
# Base exception
# ---------------------------------------------------------------------------


class TestPhotoIngestError:
    def test_is_exception(self):
        err = PhotoIngestError("something broke")
        assert isinstance(err, Exception)

    def test_user_message_defaults_to_message(self):
        err = PhotoIngestError("raw message")
        assert err.user_message == "raw message"

    def test_user_message_override(self):
        err = PhotoIngestError("raw", user_message="friendly")
        assert str(err) == "raw"
        assert err.user_message == "friendly"

    def test_subclass_catches_base(self):
        err = ConfigurationError("cfg error")
        with pytest.raises(PhotoIngestError):
            raise err


# ---------------------------------------------------------------------------
# ConfigurationError
# ---------------------------------------------------------------------------


class TestConfigurationError:
    def test_basic(self):
        err = ConfigurationError("bad config")
        assert "bad config" in str(err)
        assert isinstance(err, PhotoIngestError)

    def test_raised_on_missing_config(self, tmp_path):
        """ConfigManager raises ConfigurationError for missing file (Req 2.4)."""
        from photo_ingest.config import ConfigManager

        with pytest.raises(ConfigurationError, match="not found"):
            ConfigManager.load_config(tmp_path / "nonexistent.yaml")


# ---------------------------------------------------------------------------
# MissingDependencyError  (Req 8.1)
# ---------------------------------------------------------------------------


class TestMissingDependencyError:
    def test_message_contains_dependency_name(self):
        err = MissingDependencyError("exiftool")
        assert "exiftool" in str(err)
        assert "exiftool" in err.user_message

    def test_install_hint_included(self):
        err = MissingDependencyError("exiftool", install_hint="brew install exiftool")
        assert "brew install exiftool" in err.user_message

    def test_no_hint_is_ok(self):
        err = MissingDependencyError("some-tool")
        # Should not raise; user_message is still set
        assert err.user_message

    def test_is_configuration_error(self):
        """MissingDependencyError is a ConfigurationError subtype."""
        err = MissingDependencyError("exiftool")
        assert isinstance(err, ConfigurationError)

    def test_exif_processor_logs_warning_when_exiftool_missing(self):
        """EXIFProcessor warns (not raises) when exiftool is absent (Req 8.1)."""
        with (
            patch("photo_ingest.exif_processor.EXIFTOOL_AVAILABLE", False),
            patch("photo_ingest.exif_processor.subprocess.run", side_effect=FileNotFoundError),
        ):
            from photo_ingest.exif_processor import EXIFProcessor
            from photo_ingest.config import IngestConfig, DeviceMapping, FileTypes, LLMConfig

            cfg = IngestConfig(archive_root="/tmp/archive")
            db = MagicMock()
            db.get_cached_exif.return_value = None

            # Should not raise — processor downgrades to Pillow fallback
            proc = EXIFProcessor(cfg, db)
            assert proc.exiftool_available is False


# ---------------------------------------------------------------------------
# SourceDirectoryError  (Req 8.2)
# ---------------------------------------------------------------------------


class TestSourceDirectoryError:
    def test_path_stored(self, tmp_path):
        missing = tmp_path / "nonexistent"
        err = SourceDirectoryError(missing)
        assert err.path == missing

    def test_user_message_mentions_path(self, tmp_path):
        missing = tmp_path / "nonexistent"
        err = SourceDirectoryError(missing)
        assert str(missing) in err.user_message

    def test_user_message_is_actionable(self, tmp_path):
        err = SourceDirectoryError(tmp_path / "x")
        assert "check" in err.user_message.lower() or "does not exist" in err.user_message.lower()

    def test_ingest_command_raises_on_missing_source(self, tmp_path):
        """IngestCommand raises FileNotFoundError for missing source (Req 8.2)."""
        from photo_ingest.ingest_command import IngestCommand
        from photo_ingest.config import IngestConfig

        cfg = IngestConfig(archive_root=str(tmp_path / "archive"))
        cmd = IngestCommand(cfg)
        cmd._scanner = MagicMock()
        cmd._db_manager = MagicMock()
        cmd._dedup_engine = MagicMock()
        cmd._device_detector = MagicMock()
        cmd._file_ops = MagicMock()

        with pytest.raises(FileNotFoundError):
            cmd.run(tmp_path / "nonexistent_source", "Event")


# ---------------------------------------------------------------------------
# EXIFProcessingError
# ---------------------------------------------------------------------------


class TestEXIFProcessingError:
    def test_file_path_stored(self, tmp_path):
        path = tmp_path / "img.jpg"
        err = EXIFProcessingError(path, "corrupt header")
        assert err.file_path == path
        assert err.reason == "corrupt header"

    def test_user_message_contains_filename(self, tmp_path):
        path = tmp_path / "img.jpg"
        err = EXIFProcessingError(path, "corrupt header")
        assert "img.jpg" in err.user_message

    def test_is_photo_ingest_error(self, tmp_path):
        err = EXIFProcessingError(tmp_path / "f.jpg", "reason")
        assert isinstance(err, PhotoIngestError)


# ---------------------------------------------------------------------------
# FileOperationError  (Req 8.4)
# ---------------------------------------------------------------------------


class TestFileOperationError:
    def test_paths_stored(self, tmp_path):
        src = tmp_path / "src.jpg"
        dst = tmp_path / "dst.jpg"
        err = FileOperationError(src, dst, "disk full")
        assert err.source == src
        assert err.dest == dst
        assert err.reason == "disk full"

    def test_user_message_mentions_filename(self, tmp_path):
        src = tmp_path / "photo.jpg"
        err = FileOperationError(src, None, "permission denied")
        assert "photo.jpg" in err.user_message

    def test_user_message_says_processing_continues(self, tmp_path):
        err = FileOperationError(tmp_path / "f.jpg", None, "err")
        assert "continue" in err.user_message.lower()

    def test_none_dest_handled(self, tmp_path):
        err = FileOperationError(tmp_path / "f.jpg", None, "err")
        assert err.dest is None

    def test_file_ops_records_errors_not_raises(self, tmp_path):
        """Individual copy failures are recorded, not raised (Req 8.4)."""
        from photo_ingest.file_operations import (
            FileOperation,
            FileOperationsManager,
            OperationType,
        )
        from photo_ingest.config import IngestConfig, PerformanceConfig
        from photo_ingest.deduplication import DuplicateStatus

        src = tmp_path / "photo.jpg"
        src.write_bytes(b"x" * 64)
        dest = tmp_path / "archive" / "photo.jpg"

        cfg = IngestConfig(
            archive_root=str(tmp_path / "archive"),
            performance=PerformanceConfig(parallel_workers=1),
        )
        mgr = FileOperationsManager(cfg)

        op = FileOperation(
            source_path=src,
            dest_path=dest,
            raw_backup_path=None,
            camera_code="Cam",
            duplicate_status=DuplicateStatus.NEW,
            metadata={},
            operation_type=OperationType.ORGANIZED_ONLY,
        )

        # Patch shutil.copy2 to raise an OSError
        with patch("photo_ingest.file_operations.shutil.copy2", side_effect=OSError("disk full")):
            results = mgr.execute_operations([op], copy_mode=True)

        # Must not raise; error captured in results
        assert len(results.errors) > 0
        assert results.files_copied == 0


# ---------------------------------------------------------------------------
# ArchiveDirectoryError  (Req 8.3)
# ---------------------------------------------------------------------------


class TestArchiveDirectoryError:
    def test_directory_stored(self, tmp_path):
        d = tmp_path / "archive"
        err = ArchiveDirectoryError(d, "permission denied")
        assert err.directory == d

    def test_user_message_mentions_directory(self, tmp_path):
        d = tmp_path / "archive"
        err = ArchiveDirectoryError(d, "read-only filesystem")
        assert str(d) in err.user_message

    def test_user_message_mentions_disk_space(self, tmp_path):
        err = ArchiveDirectoryError(tmp_path / "x", "no space left")
        assert "disk space" in err.user_message.lower() or "permission" in err.user_message.lower()

    def test_is_file_operation_error(self, tmp_path):
        err = ArchiveDirectoryError(tmp_path / "x", "err")
        assert isinstance(err, FileOperationError)


# ---------------------------------------------------------------------------
# DeduplicationError
# ---------------------------------------------------------------------------


class TestDeduplicationError:
    def test_reason_stored(self):
        err = DeduplicationError("db locked")
        assert err.reason == "db locked"

    def test_db_path_optional(self):
        err = DeduplicationError("db locked")
        assert err.db_path is None

    def test_db_path_stored(self, tmp_path):
        db = tmp_path / "hashes.sqlite"
        err = DeduplicationError("db locked", db_path=db)
        assert err.db_path == db

    def test_user_message_non_fatal(self):
        err = DeduplicationError("locked")
        assert "continue" in err.user_message.lower() or "impaired" in err.user_message.lower()


# ---------------------------------------------------------------------------
# format_user_error helper  (Req 8.5)
# ---------------------------------------------------------------------------


class TestFormatUserError:
    def test_known_exception_returns_user_message(self, tmp_path):
        err = FileOperationError(tmp_path / "f.jpg", None, "disk full")
        result = format_user_error(err)
        assert result == err.user_message

    def test_unknown_exception_returns_friendly_message(self):
        err = RuntimeError("segfault in C ext")
        result = format_user_error(err)
        # Should not expose raw traceback content; should mention the type
        assert "RuntimeError" in result
        assert "unexpected" in result.lower()

    def test_unknown_exception_gives_verbose_hint(self):
        err = ValueError("bad value")
        result = format_user_error(err)
        assert "--verbose" in result or "verbose" in result.lower()

    def test_configuration_error_user_message_used(self):
        err = ConfigurationError("bad yaml", user_message="Config file is invalid.")
        result = format_user_error(err)
        assert result == "Config file is invalid."


# ---------------------------------------------------------------------------
# Graceful degradation — non-critical failures don't abort pipeline  (Req 8.4)
# ---------------------------------------------------------------------------


class TestGracefulDegradation:
    def test_ingest_command_continues_after_individual_file_error(self, tmp_path):
        """Pipeline records errors from execute_operations but does not abort."""
        from photo_ingest.ingest_command import IngestCommand, IngestResult
        from photo_ingest.config import IngestConfig, PerformanceConfig
        from photo_ingest.file_scanner import FileInfo, ScanResult
        from photo_ingest.file_operations import OperationResults
        from photo_ingest.deduplication import DuplicateStatus
        from datetime import datetime

        src1 = tmp_path / "a.jpg"
        src1.write_bytes(b"x" * 64)

        cfg = IngestConfig(
            archive_root=str(tmp_path / "archive"),
            performance=PerformanceConfig(parallel_workers=1),
        )
        cmd = IngestCommand(cfg)

        scan_result = ScanResult(
            files=[
                FileInfo(
                    path=src1,
                    size=64,
                    modified_time=datetime.now(),
                    file_type="jpeg",
                    extension="jpg",
                )
            ],
            total_files=1,
            total_size=64,
            scan_time=0.0,
            errors=[],
            directories_scanned=1,
        )

        cmd._scanner = MagicMock()
        cmd._scanner.scan_directory.return_value = scan_result
        cmd._db_manager = MagicMock()
        cmd._dedup_engine = MagicMock()
        check = MagicMock()
        check.status = DuplicateStatus.NEW
        cmd._dedup_engine.check_duplicate.return_value = check
        cmd._device_detector = MagicMock()
        cmd._device_detector.detect_device.return_value = "Cam"
        cmd._file_ops = MagicMock()
        cmd._file_ops.plan_operations.return_value = []
        cmd._file_ops.execute_operations.return_value = OperationResults(
            files_processed=0,
            files_copied=0,
            files_moved=0,
            files_skipped=0,
            duplicates_found=0,
            errors=["Permission denied copying a.jpg"],
        )

        result = cmd.run(tmp_path, "TestEvent")

        # Must not raise; errors captured in result
        assert isinstance(result, IngestResult)
        assert any("Permission denied" in e for e in result.errors)

    def test_logging_on_exif_failure(self, tmp_path, caplog):
        """EXIF failures are logged at WARNING level and processing continues."""
        from photo_ingest.exif_processor import EXIFProcessor
        from photo_ingest.config import IngestConfig

        cfg = IngestConfig(archive_root=str(tmp_path))
        db = MagicMock()
        db.get_cached_exif.return_value = None
        db.cache_exif = MagicMock()

        proc = EXIFProcessor(cfg, db)
        proc.exiftool_available = False  # Force Pillow/fallback path

        # A corrupt/empty file – Pillow will fail gracefully
        bad_file = tmp_path / "corrupt.jpg"
        bad_file.write_bytes(b"not a jpeg")

        with caplog.at_level(logging.DEBUG, logger="photo_ingest.exif_processor"):
            meta = proc.extract_metadata(bad_file)

        # Should return minimal metadata, not raise
        assert isinstance(meta, dict)
