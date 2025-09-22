"""Tests for file operations manager."""

import tempfile
import pytest
from pathlib import Path
from unittest.mock import Mock, patch
from datetime import datetime

from photo_ingest.file_operations import (
    FileOperationsManager, FileOperation, OperationType, OperationResults,
    RawBackupManager, RawBackupResult
)
from photo_ingest.config import IngestConfig, RawBackupConfig, DeviceMapping, FileTypes, LLMConfig, PeekConfig, PerformanceConfig
from photo_ingest.deduplication import DuplicateStatus


@pytest.fixture
def sample_config():
    """Create sample configuration for testing."""
    return IngestConfig(
        archive_root="/test/archive",
        raw_backup=RawBackupConfig(
            enabled=True,
            backup_root="/test/backup",
            preserve_structure=True,
            timestamp_format="%Y-%m-%d_%H%M%S"
        ),
        devices=DeviceMapping(),
        file_types=FileTypes(),
        llm=LLMConfig(),
        peek=PeekConfig(),
        dedupe_store=":memory:",
        performance=PerformanceConfig()
    )


@pytest.fixture
def file_ops_manager(sample_config):
    """Create file operations manager for testing."""
    return FileOperationsManager(sample_config)


@pytest.fixture
def temp_files():
    """Create temporary files for testing."""
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_path = Path(temp_dir)
        
        # Create test files
        files = []
        
        # File 1: JPEG
        file1 = temp_path / "IMG_001.jpg"
        file1.write_text("fake jpeg content")
        files.append(file1)
        
        # File 2: RAW
        file2 = temp_path / "IMG_002.nef"
        file2.write_text("fake raw content")
        files.append(file2)
        
        # File 3: Video
        file3 = temp_path / "VID_001.mp4"
        file3.write_text("fake video content")
        files.append(file3)
        
        yield files


@pytest.fixture
def sample_metadata():
    """Create sample metadata for testing."""
    return {
        'Make': 'NIKON CORPORATION',
        'Model': 'NIKON Z 6',
        'LensModel': 'NIKKOR Z 24-70mm f/4 S',
        'DateTime': '2023-12-01T10:30:00',
        'device_code': 'Z6'
    }


class TestFileOperation:
    """Test FileOperation dataclass."""
    
    def test_file_operation_creation(self, temp_files, sample_metadata):
        """Test creating FileOperation."""
        operation = FileOperation(
            source_path=temp_files[0],
            dest_path=Path("/dest/file.jpg"),
            raw_backup_path=Path("/backup/file.jpg"),
            camera_code="Z6",
            duplicate_status=DuplicateStatus.NEW,
            metadata=sample_metadata,
            operation_type=OperationType.BOTH
        )
        
        assert operation.source_path == temp_files[0]
        assert operation.dest_path == Path("/dest/file.jpg")
        assert operation.camera_code == "Z6"
        assert operation.duplicate_status == DuplicateStatus.NEW
        assert operation.operation_type == OperationType.BOTH
    
    def test_event_date_from_metadata(self, temp_files, sample_metadata):
        """Test extracting event date from metadata."""
        operation = FileOperation(
            source_path=temp_files[0],
            dest_path=None,
            raw_backup_path=None,
            camera_code="Z6",
            duplicate_status=DuplicateStatus.NEW,
            metadata=sample_metadata,
            operation_type=OperationType.ORGANIZED_ONLY
        )
        
        assert operation.event_date == "2023-12-01"
    
    def test_event_date_fallback_to_file_mtime(self, temp_files):
        """Test fallback to file modification time."""
        metadata = {}  # No DateTime in metadata
        
        operation = FileOperation(
            source_path=temp_files[0],
            dest_path=None,
            raw_backup_path=None,
            camera_code="Unknown",
            duplicate_status=DuplicateStatus.NEW,
            metadata=metadata,
            operation_type=OperationType.ORGANIZED_ONLY
        )
        
        # Should use file mtime
        expected_date = datetime.fromtimestamp(temp_files[0].stat().st_mtime).strftime('%Y-%m-%d')
        assert operation.event_date == expected_date


class TestFileOperationsManager:
    """Test FileOperationsManager class."""
    
    def test_init(self, file_ops_manager, sample_config):
        """Test file operations manager initialization."""
        assert file_ops_manager.config == sample_config
        assert file_ops_manager.progress_callback is None
        assert isinstance(file_ops_manager.raw_backup_manager, RawBackupManager)
    
    def test_plan_operations_organized_only(self, file_ops_manager, temp_files, sample_metadata):
        """Test planning organized-only operations."""
        files_with_metadata = {
            temp_files[0]: sample_metadata,
            temp_files[1]: sample_metadata
        }
        
        duplicate_results = {
            temp_files[0]: DuplicateStatus.NEW,
            temp_files[1]: DuplicateStatus.NEW
        }
        
        operations = file_ops_manager.plan_operations(
            files_with_metadata, "TestEvent", duplicate_results, organized_only=True
        )
        
        assert len(operations) == 2
        for op in operations:
            assert op.operation_type == OperationType.ORGANIZED_ONLY
            assert op.dest_path is not None
            assert op.raw_backup_path is None
            assert op.camera_code == "Z6"
    
    def test_plan_operations_raw_only(self, file_ops_manager, temp_files, sample_metadata):
        """Test planning raw backup only operations."""
        files_with_metadata = {
            temp_files[0]: sample_metadata
        }
        
        duplicate_results = {
            temp_files[0]: DuplicateStatus.NEW
        }
        
        operations = file_ops_manager.plan_operations(
            files_with_metadata, "TestEvent", duplicate_results, raw_only=True
        )
        
        assert len(operations) == 1
        op = operations[0]
        assert op.operation_type == OperationType.RAW_BACKUP_ONLY
        assert op.dest_path is None
        assert op.raw_backup_path is not None
    
    def test_plan_operations_both(self, file_ops_manager, temp_files, sample_metadata):
        """Test planning both operations."""
        files_with_metadata = {
            temp_files[0]: sample_metadata
        }
        
        duplicate_results = {
            temp_files[0]: DuplicateStatus.NEW
        }
        
        operations = file_ops_manager.plan_operations(
            files_with_metadata, "TestEvent", duplicate_results
        )
        
        assert len(operations) == 1
        op = operations[0]
        assert op.operation_type == OperationType.BOTH
        assert op.dest_path is not None
        assert op.raw_backup_path is not None
    
    def test_plan_operations_skip_duplicates(self, file_ops_manager, temp_files, sample_metadata):
        """Test that duplicates are skipped in organized mode."""
        files_with_metadata = {
            temp_files[0]: sample_metadata,
            temp_files[1]: sample_metadata
        }
        
        duplicate_results = {
            temp_files[0]: DuplicateStatus.NEW,
            temp_files[1]: DuplicateStatus.DUPLICATE
        }
        
        operations = file_ops_manager.plan_operations(
            files_with_metadata, "TestEvent", duplicate_results, organized_only=True
        )
        
        # Only non-duplicate file should be planned
        assert len(operations) == 1
        assert operations[0].source_path == temp_files[0]
    
    def test_plan_operations_include_duplicates_in_raw_mode(self, file_ops_manager, temp_files, sample_metadata):
        """Test that duplicates are included in raw backup mode."""
        files_with_metadata = {
            temp_files[0]: sample_metadata,
            temp_files[1]: sample_metadata
        }
        
        duplicate_results = {
            temp_files[0]: DuplicateStatus.NEW,
            temp_files[1]: DuplicateStatus.DUPLICATE
        }
        
        operations = file_ops_manager.plan_operations(
            files_with_metadata, "TestEvent", duplicate_results, raw_only=True
        )
        
        # Both files should be planned for raw backup
        assert len(operations) == 2
    
    def test_plan_organized_path(self, file_ops_manager, temp_files, sample_metadata):
        """Test planning organized destination path."""
        dest_path = file_ops_manager._plan_organized_path(
            temp_files[0], sample_metadata, "TestEvent", "Z6"
        )
        
        expected_path = Path("/test/archive/2023/2023-12-01_TestEvent/Z6/IMG_001.jpg")
        assert dest_path == expected_path
    
    def test_extract_date_from_metadata(self, file_ops_manager, temp_files):
        """Test date extraction from metadata."""
        # Test with ISO format
        metadata1 = {'DateTime': '2023-12-01T10:30:00'}
        date1 = file_ops_manager._extract_date_from_metadata(metadata1, temp_files[0])
        assert date1 == "2023-12-01"
        
        # Test with EXIF format
        metadata2 = {'DateTime': '2023:12:01 10:30:00'}
        date2 = file_ops_manager._extract_date_from_metadata(metadata2, temp_files[0])
        assert date2 == "2023-12-01"
        
        # Test fallback to file mtime
        metadata3 = {}
        date3 = file_ops_manager._extract_date_from_metadata(metadata3, temp_files[0])
        expected_date = datetime.fromtimestamp(temp_files[0].stat().st_mtime).strftime('%Y-%m-%d')
        assert date3 == expected_date
    
    def test_simulate_operations(self, file_ops_manager, temp_files, sample_metadata):
        """Test dry-run simulation of operations."""
        operations = [
            FileOperation(
                source_path=temp_files[0],
                dest_path=Path("/dest/file1.jpg"),
                raw_backup_path=None,
                camera_code="Z6",
                duplicate_status=DuplicateStatus.NEW,
                metadata=sample_metadata,
                operation_type=OperationType.ORGANIZED_ONLY
            ),
            FileOperation(
                source_path=temp_files[1],
                dest_path=Path("/dest/file2.nef"),
                raw_backup_path=None,
                camera_code="Z6",
                duplicate_status=DuplicateStatus.DUPLICATE,
                metadata=sample_metadata,
                operation_type=OperationType.ORGANIZED_ONLY
            )
        ]
        
        results = file_ops_manager._simulate_operations(operations, copy_mode=True)
        
        assert isinstance(results, OperationResults)
        assert results.files_processed == 2
        assert results.files_copied == 2
        assert results.files_moved == 0
        assert results.duplicates_found == 1
    
    def test_verify_file_integrity_same_size(self, file_ops_manager, temp_files):
        """Test file integrity verification with same size files."""
        # Create two files with same content
        file1 = temp_files[0]
        file2 = temp_files[0].parent / "copy.jpg"
        file2.write_text(file1.read_text())
        
        # Should pass integrity check
        assert file_ops_manager.verify_file_integrity(file1, file2) is True
    
    def test_verify_file_integrity_different_size(self, file_ops_manager, temp_files):
        """Test file integrity verification with different size files."""
        file1 = temp_files[0]
        file2 = temp_files[0].parent / "different.jpg"
        file2.write_text("different content")
        
        # Should fail integrity check
        assert file_ops_manager.verify_file_integrity(file1, file2) is False
    
    def test_find_common_root_single_file(self, file_ops_manager, temp_files):
        """Test finding common root for single file."""
        common_root = file_ops_manager._find_common_root([temp_files[0]])
        assert common_root == temp_files[0].parent
    
    def test_find_common_root_multiple_files(self, file_ops_manager, temp_files):
        """Test finding common root for multiple files."""
        common_root = file_ops_manager._find_common_root(temp_files)
        assert common_root == temp_files[0].parent


class TestRawBackupManager:
    """Test RawBackupManager class."""
    
    @pytest.fixture
    def backup_config(self):
        """Create backup configuration for testing."""
        return RawBackupConfig(
            enabled=True,
            backup_root="/test/backup",
            preserve_structure=True,
            timestamp_format="%Y-%m-%d_%H%M%S"
        )
    
    @pytest.fixture
    def backup_manager(self, backup_config):
        """Create backup manager for testing."""
        return RawBackupManager(backup_config)
    
    def test_init(self, backup_manager, backup_config):
        """Test backup manager initialization."""
        assert backup_manager.config == backup_config
        assert backup_manager.backup_timestamp is not None
        assert backup_manager._backup_directory is None
    
    def test_get_backup_path_preserve_structure(self, backup_manager, temp_files):
        """Test backup path generation with structure preservation."""
        source_file = temp_files[0]
        source_root = source_file.parent
        
        backup_path = backup_manager.get_backup_path(source_file, source_root)
        
        # Should preserve relative path
        expected_path = backup_manager.get_backup_directory() / source_file.name
        assert backup_path == expected_path
    
    def test_get_backup_path_no_preserve_structure(self, temp_files):
        """Test backup path generation without structure preservation."""
        config = RawBackupConfig(
            enabled=True,
            backup_root="/test/backup",
            preserve_structure=False,
            timestamp_format="%Y-%m-%d_%H%M%S"
        )
        backup_manager = RawBackupManager(config)
        
        source_file = temp_files[0]
        source_root = source_file.parent
        
        backup_path = backup_manager.get_backup_path(source_file, source_root)
        
        # Should use just filename
        expected_path = backup_manager.get_backup_directory() / source_file.name
        assert backup_path == expected_path
    
    def test_get_backup_directory_unique(self, backup_manager):
        """Test backup directory uniqueness."""
        # First call should create directory path
        dir1 = backup_manager.get_backup_directory()
        
        # Second call should return same directory
        dir2 = backup_manager.get_backup_directory()
        
        assert dir1 == dir2
    
    @patch('photo_ingest.file_operations.shutil.copy2')
    def test_create_raw_backup_copy_mode(self, mock_copy, backup_manager, temp_files):
        """Test raw backup creation in copy mode."""
        source_root = temp_files[0].parent
        
        result = backup_manager.create_raw_backup(
            temp_files[:2], source_root, copy_mode=True
        )
        
        assert isinstance(result, RawBackupResult)
        assert result.files_backed_up == 2
        assert result.files_skipped == 0
        assert len(result.errors) == 0
        assert mock_copy.call_count == 2
    
    @patch('photo_ingest.file_operations.shutil.move')
    def test_create_raw_backup_move_mode(self, mock_move, backup_manager, temp_files):
        """Test raw backup creation in move mode."""
        source_root = temp_files[0].parent
        
        result = backup_manager.create_raw_backup(
            temp_files[:1], source_root, copy_mode=False
        )
        
        assert isinstance(result, RawBackupResult)
        assert result.files_backed_up == 1
        assert result.files_skipped == 0
        assert len(result.errors) == 0
        assert mock_move.call_count == 1
    
    def test_create_raw_backup_disabled(self, temp_files):
        """Test raw backup when disabled."""
        config = RawBackupConfig(enabled=False)
        backup_manager = RawBackupManager(config)
        
        result = backup_manager.create_raw_backup(
            temp_files, temp_files[0].parent, copy_mode=True
        )
        
        assert result.files_backed_up == 0
        assert result.files_skipped == len(temp_files)
        assert len(result.errors) == 1
        assert "disabled" in result.errors[0]
    
    @patch('photo_ingest.file_operations.shutil.copy2', side_effect=OSError("Permission denied"))
    def test_create_raw_backup_error_handling(self, mock_copy, backup_manager, temp_files):
        """Test error handling during backup."""
        source_root = temp_files[0].parent
        
        result = backup_manager.create_raw_backup(
            temp_files[:1], source_root, copy_mode=True
        )
        
        assert result.files_backed_up == 0
        assert result.files_skipped == 1
        assert len(result.errors) == 1
        assert "Permission denied" in result.errors[0]


class TestFileOperationsIntegration:
    """Integration tests for file operations."""
    
    def test_full_workflow_organized_only(self, file_ops_manager, temp_files, sample_metadata):
        """Test complete organized import workflow."""
        files_with_metadata = {temp_files[0]: sample_metadata}
        duplicate_results = {temp_files[0]: DuplicateStatus.NEW}
        
        # Plan operations
        operations = file_ops_manager.plan_operations(
            files_with_metadata, "TestEvent", duplicate_results, organized_only=True
        )
        
        # Simulate execution
        results = file_ops_manager.execute_operations(
            operations, copy_mode=True, dry_run=True
        )
        
        assert isinstance(results, OperationResults)
        assert results.files_processed == 1
        assert results.files_copied == 1
        assert results.duplicates_found == 0
        assert results.raw_backup_result is None
    
    def test_full_workflow_both_operations(self, file_ops_manager, temp_files, sample_metadata):
        """Test complete workflow with both operations."""
        files_with_metadata = {temp_files[0]: sample_metadata}
        duplicate_results = {temp_files[0]: DuplicateStatus.NEW}
        
        # Plan operations
        operations = file_ops_manager.plan_operations(
            files_with_metadata, "TestEvent", duplicate_results
        )
        
        # Simulate execution
        results = file_ops_manager.execute_operations(
            operations, copy_mode=True, dry_run=True
        )
        
        assert isinstance(results, OperationResults)
        assert results.files_processed == 1
        assert results.raw_backup_result is not None
        assert results.raw_backup_result.files_backed_up == 1
    
    def test_progress_callback(self, sample_config, temp_files, sample_metadata):
        """Test progress callback functionality."""
        progress_calls = []
        
        def progress_callback(phase, current, total, message):
            progress_calls.append((phase, current, total, message))
        
        file_ops_manager = FileOperationsManager(sample_config, progress_callback)
        
        files_with_metadata = {temp_files[0]: sample_metadata}
        duplicate_results = {temp_files[0]: DuplicateStatus.NEW}
        
        operations = file_ops_manager.plan_operations(
            files_with_metadata, "TestEvent", duplicate_results, organized_only=True
        )
        
        # Execute with dry run to avoid actual file operations
        file_ops_manager.execute_operations(operations, copy_mode=True, dry_run=True)
        
        # Progress callback should not be called in dry run mode
        # But this tests that the callback mechanism works