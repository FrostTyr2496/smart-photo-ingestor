"""Tests for file scanning and discovery functionality."""

import os
import tempfile
import pytest
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import Mock, patch, MagicMock

from photo_ingest.file_scanner import (
    FileScanner, FileInfo, ScanResult, DirectoryValidator
)
from photo_ingest.config import FileTypes, PerformanceConfig


class TestFileInfo:
    """Test FileInfo dataclass."""
    
    def test_file_info_creation(self):
        """Test FileInfo creation with basic attributes."""
        path = Path("/test/image.jpg")
        modified_time = datetime.now()
        
        file_info = FileInfo(
            path=path,
            size=1024,
            modified_time=modified_time,
            file_type="jpeg",
            extension="jpg"
        )
        
        assert file_info.path == path
        assert file_info.size == 1024
        assert file_info.modified_time == modified_time
        assert file_info.file_type == "jpeg"
        assert file_info.extension == "jpg"
        assert file_info.mime_type == "image/jpeg"  # Set by __post_init__
    
    def test_file_info_mime_type_override(self):
        """Test FileInfo with explicit mime type."""
        file_info = FileInfo(
            path=Path("/test/image.nef"),
            size=1024,
            modified_time=datetime.now(),
            file_type="raw",
            extension="nef",
            mime_type="image/x-nikon-nef"
        )
        
        assert file_info.mime_type == "image/x-nikon-nef"


class TestScanResult:
    """Test ScanResult dataclass."""
    
    def test_scan_result_creation(self):
        """Test ScanResult creation."""
        files = [
            FileInfo(Path("/test/img1.jpg"), 1024, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/test/img2.nef"), 2048, datetime.now(), "raw", "nef"),
            FileInfo(Path("/test/vid1.mp4"), 4096, datetime.now(), "video", "mp4")
        ]
        
        result = ScanResult(
            files=files,
            total_files=3,
            total_size=7168,
            scan_time=1.5,
            errors=[],
            directories_scanned=1
        )
        
        assert len(result.files) == 3
        assert result.total_files == 3
        assert result.total_size == 7168
        assert result.scan_time == 1.5
        assert result.directories_scanned == 1
    
    def test_files_by_type(self):
        """Test grouping files by type."""
        files = [
            FileInfo(Path("/test/img1.jpg"), 1024, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/test/img2.jpg"), 1024, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/test/img3.nef"), 2048, datetime.now(), "raw", "nef"),
            FileInfo(Path("/test/vid1.mp4"), 4096, datetime.now(), "video", "mp4")
        ]
        
        result = ScanResult(files, 4, 8192, 1.0, [], 1)
        by_type = result.files_by_type
        
        assert len(by_type["jpeg"]) == 2
        assert len(by_type["raw"]) == 1
        assert len(by_type["video"]) == 1
    
    def test_size_by_type(self):
        """Test calculating size by type."""
        files = [
            FileInfo(Path("/test/img1.jpg"), 1024, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/test/img2.jpg"), 2048, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/test/img3.nef"), 4096, datetime.now(), "raw", "nef"),
            FileInfo(Path("/test/vid1.mp4"), 8192, datetime.now(), "video", "mp4")
        ]
        
        result = ScanResult(files, 4, 15360, 1.0, [], 1)
        size_by_type = result.size_by_type
        
        assert size_by_type["jpeg"] == 3072  # 1024 + 2048
        assert size_by_type["raw"] == 4096
        assert size_by_type["video"] == 8192


class TestFileScanner:
    """Test FileScanner class."""
    
    @pytest.fixture
    def file_types(self):
        """Default file types configuration."""
        return FileTypes(
            raw=["nef", "cr3", "dng"],
            jpeg=["jpg", "jpeg", "heic"],
            video=["mp4", "mov"]
        )
    
    @pytest.fixture
    def performance_config(self):
        """Default performance configuration."""
        return PerformanceConfig(parallel_workers=2, batch_size=10)
    
    @pytest.fixture
    def scanner(self, file_types, performance_config):
        """FileScanner instance for testing."""
        return FileScanner(file_types, performance_config)
    
    @pytest.fixture
    def temp_dir_with_files(self):
        """Create temporary directory with test files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            
            # Create test files
            (temp_path / "image1.jpg").write_text("fake jpeg content")
            (temp_path / "image2.nef").write_text("fake raw content")
            (temp_path / "video1.mp4").write_text("fake video content")
            (temp_path / "document.txt").write_text("not a photo")
            
            # Create subdirectory with files
            sub_dir = temp_path / "subdir"
            sub_dir.mkdir()
            (sub_dir / "image3.jpg").write_text("fake jpeg in subdir")
            (sub_dir / "image4.cr3").write_text("fake raw in subdir")
            
            yield temp_path
    
    def test_scanner_initialization(self, file_types, performance_config):
        """Test FileScanner initialization."""
        progress_callback = Mock()
        scanner = FileScanner(file_types, performance_config, progress_callback)
        
        assert scanner.file_types == file_types
        assert scanner.performance_config == performance_config
        assert scanner.progress_callback == progress_callback
        
        # Check supported extensions
        expected_extensions = {"nef", "cr3", "dng", "jpg", "jpeg", "heic", "mp4", "mov"}
        assert scanner._supported_extensions == expected_extensions
        
        # Check extension to type mapping
        assert scanner._extension_to_type["jpg"] == "jpeg"
        assert scanner._extension_to_type["nef"] == "raw"
        assert scanner._extension_to_type["mp4"] == "video"
    
    def test_is_supported_file(self, scanner, temp_dir_with_files):
        """Test supported file detection."""
        temp_path = temp_dir_with_files
        
        assert scanner._is_supported_file(temp_path / "image1.jpg") is True
        assert scanner._is_supported_file(temp_path / "image2.nef") is True
        assert scanner._is_supported_file(temp_path / "video1.mp4") is True
        assert scanner._is_supported_file(temp_path / "document.txt") is False
        assert scanner._is_supported_file(temp_path / "nonexistent.jpg") is False
    
    def test_get_file_type(self, scanner):
        """Test file type detection."""
        assert scanner._get_file_type(Path("test.jpg")) == "jpeg"
        assert scanner._get_file_type(Path("test.JPG")) == "jpeg"
        assert scanner._get_file_type(Path("test.nef")) == "raw"
        assert scanner._get_file_type(Path("test.mp4")) == "video"
        assert scanner._get_file_type(Path("test.txt")) == "unknown"
    
    def test_scan_directory_recursive(self, scanner, temp_dir_with_files):
        """Test recursive directory scanning."""
        result = scanner.scan_directory(temp_dir_with_files, recursive=True)
        
        assert isinstance(result, ScanResult)
        assert result.total_files == 5  # 3 in root + 2 in subdir
        assert result.directories_scanned == 2  # root + subdir
        assert result.scan_time > 0
        
        # Check file types
        by_type = result.files_by_type
        assert len(by_type["jpeg"]) == 2  # image1.jpg + image3.jpg
        assert len(by_type["raw"]) == 2   # image2.nef + image4.cr3
        assert len(by_type["video"]) == 1 # video1.mp4
    
    def test_scan_directory_non_recursive(self, scanner, temp_dir_with_files):
        """Test non-recursive directory scanning."""
        result = scanner.scan_directory(temp_dir_with_files, recursive=False)
        
        assert result.total_files == 3  # Only files in root directory
        assert result.directories_scanned == 1  # Only root directory
        
        # Check that subdirectory files are not included
        file_names = [f.path.name for f in result.files]
        assert "image1.jpg" in file_names
        assert "image2.nef" in file_names
        assert "video1.mp4" in file_names
        assert "image3.jpg" not in file_names  # In subdirectory
        assert "image4.cr3" not in file_names  # In subdirectory
    
    def test_scan_nonexistent_directory(self, scanner):
        """Test scanning nonexistent directory."""
        with pytest.raises(FileNotFoundError):
            scanner.scan_directory(Path("/nonexistent/directory"))
    
    def test_scan_file_instead_of_directory(self, scanner, temp_dir_with_files):
        """Test scanning a file instead of directory."""
        file_path = temp_dir_with_files / "image1.jpg"
        with pytest.raises(ValueError, match="Path is not a directory"):
            scanner.scan_directory(file_path)
    
    def test_progress_callback(self, file_types, performance_config, temp_dir_with_files):
        """Test progress callback functionality."""
        progress_callback = Mock()
        scanner = FileScanner(file_types, performance_config, progress_callback)
        
        result = scanner.scan_directory(temp_dir_with_files, recursive=True)
        
        # Progress callback should have been called
        assert progress_callback.call_count > 0
        
        # Check that final call has correct total
        final_call = progress_callback.call_args_list[-1]
        assert final_call[0][1] == result.total_files  # total files
    
    def test_create_file_info(self, scanner, temp_dir_with_files):
        """Test FileInfo creation."""
        file_path = temp_dir_with_files / "image1.jpg"
        file_info = scanner._create_file_info(file_path)
        
        assert file_info.path == file_path
        assert file_info.size > 0
        assert isinstance(file_info.modified_time, datetime)
        assert file_info.file_type == "jpeg"
        assert file_info.extension == "jpg"
        assert file_info.mime_type == "image/jpeg"
    
    def test_filter_by_size(self, scanner):
        """Test filtering files by size."""
        files = [
            FileInfo(Path("/test/small.jpg"), 100, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/test/medium.jpg"), 1000, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/test/large.jpg"), 10000, datetime.now(), "jpeg", "jpg")
        ]
        
        # Filter by minimum size
        filtered = scanner.filter_by_size(files, min_size=500)
        assert len(filtered) == 2
        assert all(f.size >= 500 for f in filtered)
        
        # Filter by size range
        filtered = scanner.filter_by_size(files, min_size=500, max_size=5000)
        assert len(filtered) == 1
        assert filtered[0].size == 1000
    
    def test_filter_by_type(self, scanner):
        """Test filtering files by type."""
        files = [
            FileInfo(Path("/test/img.jpg"), 1000, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/test/raw.nef"), 2000, datetime.now(), "raw", "nef"),
            FileInfo(Path("/test/vid.mp4"), 3000, datetime.now(), "video", "mp4")
        ]
        
        # Filter for JPEG only
        filtered = scanner.filter_by_type(files, ["jpeg"])
        assert len(filtered) == 1
        assert filtered[0].file_type == "jpeg"
        
        # Filter for multiple types
        filtered = scanner.filter_by_type(files, ["jpeg", "raw"])
        assert len(filtered) == 2
        assert all(f.file_type in ["jpeg", "raw"] for f in filtered)
    
    def test_filter_by_date_range(self, scanner):
        """Test filtering files by date range."""
        now = datetime.now()
        yesterday = now - timedelta(days=1)
        tomorrow = now + timedelta(days=1)
        
        files = [
            FileInfo(Path("/test/old.jpg"), 1000, yesterday, "jpeg", "jpg"),
            FileInfo(Path("/test/current.jpg"), 1000, now, "jpeg", "jpg"),
            FileInfo(Path("/test/future.jpg"), 1000, tomorrow, "jpeg", "jpg")
        ]
        
        # Filter from now onwards
        filtered = scanner.filter_by_date_range(files, start_date=now)
        assert len(filtered) == 2
        assert all(f.modified_time >= now for f in filtered)
        
        # Filter up to now
        filtered = scanner.filter_by_date_range(files, end_date=now)
        assert len(filtered) == 2
        assert all(f.modified_time <= now for f in filtered)
        
        # Filter specific range
        filtered = scanner.filter_by_date_range(files, start_date=now, end_date=now)
        assert len(filtered) == 1
        assert filtered[0].modified_time == now
    
    def test_group_by_directory(self, scanner):
        """Test grouping files by directory."""
        files = [
            FileInfo(Path("/dir1/file1.jpg"), 1000, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/dir1/file2.jpg"), 1000, datetime.now(), "jpeg", "jpg"),
            FileInfo(Path("/dir2/file3.jpg"), 1000, datetime.now(), "jpeg", "jpg")
        ]
        
        groups = scanner.group_by_directory(files)
        
        assert len(groups) == 2
        assert len(groups[Path("/dir1")]) == 2
        assert len(groups[Path("/dir2")]) == 1
    
    def test_get_summary_stats(self, scanner):
        """Test summary statistics generation."""
        now = datetime.now()
        files = [
            FileInfo(Path("/test/img1.jpg"), 1000, now, "jpeg", "jpg"),
            FileInfo(Path("/test/img2.jpeg"), 2000, now, "jpeg", "jpeg"),
            FileInfo(Path("/test/raw1.nef"), 5000, now, "raw", "nef"),
            FileInfo(Path("/test/vid1.mp4"), 10000, now, "video", "mp4")
        ]
        
        stats = scanner.get_summary_stats(files)
        
        assert stats["total_files"] == 4
        assert stats["total_size"] == 18000
        assert stats["by_type"]["jpeg"] == 2
        assert stats["by_type"]["raw"] == 1
        assert stats["by_type"]["video"] == 1
        assert stats["by_extension"]["jpg"] == 1
        assert stats["by_extension"]["jpeg"] == 1
        assert stats["by_extension"]["nef"] == 1
        assert stats["by_extension"]["mp4"] == 1
        assert stats["largest_file"].size == 10000
        assert stats["smallest_file"].size == 1000
    
    def test_get_summary_stats_empty(self, scanner):
        """Test summary statistics for empty file list."""
        stats = scanner.get_summary_stats([])
        
        assert stats["total_files"] == 0
        assert stats["total_size"] == 0
        assert stats["by_type"] == {}
        assert stats["by_extension"] == {}
        assert stats["date_range"] is None
        assert stats["largest_file"] is None
        assert stats["smallest_file"] is None
    
    @patch('photo_ingest.file_scanner.ThreadPoolExecutor')
    def test_parallel_processing(self, mock_executor, scanner, temp_dir_with_files):
        """Test parallel file processing."""
        # Mock the executor to verify it's used
        mock_executor_instance = MagicMock()
        mock_executor.return_value.__enter__.return_value = mock_executor_instance
        
        # Mock futures
        mock_future = MagicMock()
        mock_future.result.return_value = FileInfo(
            Path("/test/file.jpg"), 1000, datetime.now(), "jpeg", "jpg"
        )
        mock_executor_instance.submit.return_value = mock_future
        
        # Mock as_completed to return our mock future
        with patch('photo_ingest.file_scanner.as_completed', return_value=[mock_future]):
            files = [temp_dir_with_files / "image1.jpg"]
            result = scanner._extract_file_info_parallel(files)
        
        # Verify executor was used
        mock_executor.assert_called_once_with(max_workers=scanner.performance_config.parallel_workers)
        assert len(result) == 1


class TestDirectoryValidator:
    """Test DirectoryValidator class."""
    
    @pytest.fixture
    def temp_dir(self):
        """Create temporary directory for testing."""
        with tempfile.TemporaryDirectory() as temp_dir:
            yield Path(temp_dir)
    
    def test_validate_existing_directory(self, temp_dir):
        """Test validation of existing directory."""
        # Add a file to make directory non-empty
        (temp_dir / "test.txt").write_text("test content")
        
        is_valid, errors = DirectoryValidator.validate_source_directory(temp_dir)
        
        assert is_valid is True
        assert len(errors) == 0
    
    def test_validate_nonexistent_directory(self):
        """Test validation of nonexistent directory."""
        nonexistent = Path("/nonexistent/directory")
        
        is_valid, errors = DirectoryValidator.validate_source_directory(nonexistent)
        
        assert is_valid is False
        assert len(errors) == 1
        assert "does not exist" in errors[0]
    
    def test_validate_file_instead_of_directory(self, temp_dir):
        """Test validation when path is a file."""
        file_path = temp_dir / "test.txt"
        file_path.write_text("test content")
        
        is_valid, errors = DirectoryValidator.validate_source_directory(file_path)
        
        assert is_valid is False
        assert len(errors) == 1
        assert "not a directory" in errors[0]
    
    def test_validate_empty_directory(self, temp_dir):
        """Test validation of empty directory."""
        is_valid, errors = DirectoryValidator.validate_source_directory(temp_dir)
        
        assert is_valid is False
        assert len(errors) == 1
        assert "empty" in errors[0]
    
    def test_check_directory_permissions(self, temp_dir):
        """Test directory permissions checking."""
        permissions = DirectoryValidator.check_directory_permissions(temp_dir)
        
        assert permissions["exists"] is True
        assert permissions["is_dir"] is True
        assert permissions["readable"] is True
        assert permissions["writable"] is True
        assert permissions["executable"] is True
    
    def test_check_nonexistent_directory_permissions(self):
        """Test permissions checking for nonexistent directory."""
        nonexistent = Path("/nonexistent/directory")
        permissions = DirectoryValidator.check_directory_permissions(nonexistent)
        
        assert permissions["exists"] is False
        assert permissions["is_dir"] is False
        assert permissions["readable"] is False
        assert permissions["writable"] is False
        assert permissions["executable"] is False


if __name__ == "__main__":
    pytest.main([__file__])